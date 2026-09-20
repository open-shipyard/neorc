# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The credential store, in dictionaries."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from neorc_core._access import ApiToken, PendingLogin, Principal, Role
from neorc_core._errors import InvalidValueError
from neorc_core.ports._credentials import CredentialStore


class MemoryCredentialStore(CredentialStore):
    """Tokens, sessions and pending logins in dictionaries: one lock per operation."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tokens: dict[str, tuple[str, ApiToken]] = {}
        """By name: the hash and the token."""
        self._sessions: dict[str, tuple[Principal, datetime]] = {}
        self._logins: dict[str, tuple[PendingLogin, datetime]] = {}

    async def add_token(
        self,
        name: str,
        secret_hash: str,
        *,
        role: Role,
        queue: str | None = None,
        expires_seconds: float | None = None,
    ) -> ApiToken:
        async with self._lock:
            if name in self._tokens:
                raise InvalidValueError(f"a token called {name!r} exists")
            now = datetime.now(UTC)
            token = ApiToken(
                id=uuid.uuid4(),
                name=name,
                role=role,
                created_at=now,
                queue=queue,
                expires_at=_after(now, expires_seconds),
            )
            self._tokens[name] = (secret_hash, token)
            return token

    async def token_by_hash(self, secret_hash: str) -> ApiToken | None:
        async with self._lock:
            now = datetime.now(UTC)
            for kept, token in self._tokens.values():
                if kept == secret_hash:
                    return token if _alive(token.expires_at, now) else None
            return None

    async def tokens(self) -> list[ApiToken]:
        async with self._lock:
            return [token for _, (_, token) in sorted(self._tokens.items())]

    async def delete_token(self, name: str) -> bool:
        async with self._lock:
            return self._tokens.pop(name, None) is not None

    async def add_session(
        self, secret_hash: str, principal: Principal, *, seconds: float
    ) -> None:
        async with self._lock:
            now = datetime.now(UTC)
            self._sessions[secret_hash] = (principal, now + timedelta(seconds=seconds))

    async def session_by_hash(self, secret_hash: str) -> Principal | None:
        async with self._lock:
            found = self._sessions.get(secret_hash)
            if found is None or not _alive(found[1], datetime.now(UTC)):
                return None
            return found[0]

    async def delete_session(self, secret_hash: str) -> bool:
        async with self._lock:
            return self._sessions.pop(secret_hash, None) is not None

    async def delete_sessions(self) -> int:
        async with self._lock:
            count = len(self._sessions)
            self._sessions.clear()
            return count

    async def add_login(
        self, state_hash: str, login: PendingLogin, *, seconds: float, limit: int
    ) -> bool:
        async with self._lock:
            now = datetime.now(UTC)
            alive = sum(
                1 for _, expires in self._logins.values() if _alive(expires, now)
            )
            if alive >= limit:
                return False
            self._logins[state_hash] = (login, now + timedelta(seconds=seconds))
            return True

    async def take_login(self, state_hash: str) -> PendingLogin | None:
        async with self._lock:
            found = self._logins.pop(state_hash, None)
            if found is None or not _alive(found[1], datetime.now(UTC)):
                return None
            return found[0]

    async def delete_expired(self) -> None:
        async with self._lock:
            now = datetime.now(UTC)
            self._sessions = {
                key: kept
                for key, kept in self._sessions.items()
                if _alive(kept[1], now)
            }
            self._logins = {
                key: kept for key, kept in self._logins.items() if _alive(kept[1], now)
            }


def _after(now: datetime, seconds: float | None) -> datetime | None:
    return None if seconds is None else now + timedelta(seconds=seconds)


def _alive(expires_at: datetime | None, now: datetime) -> bool:
    return expires_at is None or now < expires_at
