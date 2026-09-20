# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The Postgres store for API tokens, sessions and sign-ins in progress.

Each operation is one statement, so one transaction, and none takes a lock
beyond the rows it writes: nothing here joins the flow store's lock order.
Expiry is compared with the server's ``now()``, which also sets every time,
so one clock decides.
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import errors

from neorc.postgres._pool import Pooled
from neorc.postgres._schema import PENDING_LOGINS_TABLE, SESSIONS_TABLE, TOKENS_TABLE
from neorc_core import ApiToken, CredentialStore, InvalidValueError
from neorc_core._access import PendingLogin, Principal, PrincipalKind, Role

_TOKEN_COLUMNS = "id, name, role, queue, created_at, expires_at"

_INSERT_TOKEN = f"""
INSERT INTO {TOKENS_TABLE} (id, name, secret_hash, role, queue, created_at,
                           expires_at)
VALUES (%(id)s, %(name)s, %(hash)s, %(role)s, %(queue)s, now(),
        now() + make_interval(secs => %(seconds)s::double precision))
RETURNING {_TOKEN_COLUMNS}
"""

_ALIVE = "(expires_at IS NULL OR now() < expires_at)"

_TOKEN_BY_HASH = f"""
SELECT {_TOKEN_COLUMNS} FROM {TOKENS_TABLE}
 WHERE secret_hash = %(hash)s AND {_ALIVE}
"""

_TOKENS = f"SELECT {_TOKEN_COLUMNS} FROM {TOKENS_TABLE} ORDER BY name"

_DELETE_TOKEN = f"DELETE FROM {TOKENS_TABLE} WHERE name = %(name)s"

_INSERT_SESSION = f"""
INSERT INTO {SESSIONS_TABLE} (secret_hash, kind, subject, name, role, queue,
                              provider, email, created_at, expires_at)
VALUES (%(hash)s, %(kind)s, %(subject)s, %(name)s, %(role)s, %(queue)s,
        %(provider)s, %(email)s, now(),
        now() + make_interval(secs => %(seconds)s::double precision))
"""

_SESSION_BY_HASH = f"""
SELECT kind, subject, name, role, queue, provider, email FROM {SESSIONS_TABLE}
 WHERE secret_hash = %(hash)s AND now() < expires_at
"""

_DELETE_SESSION = f"DELETE FROM {SESSIONS_TABLE} WHERE secret_hash = %(hash)s"

_DELETE_SESSIONS = f"DELETE FROM {SESSIONS_TABLE}"

# The count and the insert are one statement. Two at once may both count
# under the limit, so it can be passed by the number of concurrent sign-ins:
# a ceiling on growth, not an exact number, which is all it is for.
_INSERT_LOGIN = f"""
INSERT INTO {PENDING_LOGINS_TABLE} (state_hash, provider, nonce, verifier,
                                    created_at, expires_at)
SELECT %(hash)s, %(provider)s, %(nonce)s, %(verifier)s,
       now(), now() + make_interval(secs => %(seconds)s::double precision)
 WHERE (SELECT count(*) FROM {PENDING_LOGINS_TABLE}
         WHERE expires_at > now()) < %(limit)s
"""

# Deleting and reading are one statement: of two takes at once, the second
# waits for the first's row lock and then finds the row gone.
_TAKE_LOGIN = f"""
DELETE FROM {PENDING_LOGINS_TABLE}
 WHERE state_hash = %(hash)s
RETURNING provider, nonce, verifier, now() < expires_at AS alive
"""

_DELETE_EXPIRED_SESSIONS = f"DELETE FROM {SESSIONS_TABLE} WHERE expires_at <= now()"
_DELETE_EXPIRED_LOGINS = f"DELETE FROM {PENDING_LOGINS_TABLE} WHERE expires_at <= now()"


class PostgresCredentialStore(Pooled, CredentialStore):
    """API tokens, sessions and pending logins in Postgres, over a small pool."""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 4) -> None:
        super().__init__(dsn, min_size=min_size, max_size=max_size)

    async def add_token(
        self,
        name: str,
        secret_hash: str,
        *,
        role: Role,
        queue: str | None = None,
        expires_seconds: float | None = None,
    ) -> ApiToken:
        params = {
            "id": uuid.uuid4(),
            "name": name,
            "hash": secret_hash,
            "role": Role(role).value,
            "queue": queue,
            "seconds": expires_seconds,
        }
        try:
            async with self.pool.connection() as conn:
                cursor = await conn.execute(_INSERT_TOKEN, params)
                row = await cursor.fetchone()
        except errors.UniqueViolation as exc:
            if exc.diag.constraint_name == f"{TOKENS_TABLE}_name_key":
                raise InvalidValueError(f"a token called {name!r} exists") from None
            raise
        assert row is not None
        return _token(row)

    async def token_by_hash(self, secret_hash: str) -> ApiToken | None:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(_TOKEN_BY_HASH, {"hash": secret_hash})
            row = await cursor.fetchone()
        return None if row is None else _token(row)

    async def tokens(self) -> list[ApiToken]:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(_TOKENS)
            return [_token(row) for row in await cursor.fetchall()]

    async def delete_token(self, name: str) -> bool:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(_DELETE_TOKEN, {"name": name})
            return cursor.rowcount > 0

    async def add_session(
        self, secret_hash: str, principal: Principal, *, seconds: float
    ) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                _INSERT_SESSION,
                {
                    "hash": secret_hash,
                    "kind": principal.kind.value,
                    "subject": principal.subject,
                    "name": principal.name,
                    "role": principal.role.value,
                    "queue": principal.queue,
                    "provider": principal.provider,
                    "email": principal.email,
                    "seconds": seconds,
                },
            )

    async def session_by_hash(self, secret_hash: str) -> Principal | None:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(_SESSION_BY_HASH, {"hash": secret_hash})
            row = await cursor.fetchone()
        if row is None:
            return None
        return Principal(
            kind=PrincipalKind(row["kind"]),
            subject=row["subject"],
            name=row["name"],
            role=Role(row["role"]),
            queue=row["queue"],
            provider=row["provider"],
            email=row["email"],
        )

    async def delete_session(self, secret_hash: str) -> bool:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(_DELETE_SESSION, {"hash": secret_hash})
            return cursor.rowcount > 0

    async def delete_sessions(self) -> int:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(_DELETE_SESSIONS)
            return cursor.rowcount

    async def add_login(
        self, state_hash: str, login: PendingLogin, *, seconds: float, limit: int
    ) -> bool:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                _INSERT_LOGIN,
                {
                    "hash": state_hash,
                    "provider": login.provider,
                    "nonce": login.nonce,
                    "verifier": login.verifier,
                    "seconds": seconds,
                    "limit": limit,
                },
            )
            return cursor.rowcount > 0

    async def take_login(self, state_hash: str) -> PendingLogin | None:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(_TAKE_LOGIN, {"hash": state_hash})
            row = await cursor.fetchone()
        if row is None or not row["alive"]:
            return None
        return PendingLogin(
            provider=row["provider"], nonce=row["nonce"], verifier=row["verifier"]
        )

    async def delete_expired(self) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(_DELETE_EXPIRED_SESSIONS)
            await conn.execute(_DELETE_EXPIRED_LOGINS)


def _token(row: Any) -> ApiToken:
    return ApiToken(
        id=row["id"],
        name=row["name"],
        role=Role(row["role"]),
        created_at=row["created_at"],
        queue=row["queue"],
        expires_at=row["expires_at"],
    )
