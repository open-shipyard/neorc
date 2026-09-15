# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The port a manager keeps API tokens, sessions and sign-ins in progress behind.

Every method is one atomic operation, as on the store for flows. Secrets never
reach it: it is handed their SHA-256, as ``secret_hash`` gives it, and finds
rows by that. The store keeps the clock: it is given how long a row lives, and
a find never returns a row past its expiry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from neorc_core._access import ApiToken, PendingLogin, Principal


class CredentialStore(ABC):
    """Durable storage for API tokens, sessions and pending logins."""

    @abstractmethod
    async def add_token(
        self, name: str, secret_hash: str, *, expires_seconds: float | None = None
    ) -> ApiToken:
        """Store a new token, expiring ``expires_seconds`` from now, or never.

        Raises ``InvalidValueError`` if a token called ``name`` exists, expired
        or not.
        """
        raise NotImplementedError

    @abstractmethod
    async def token_by_hash(self, secret_hash: str) -> ApiToken | None:
        """The token with that hash, unless there is none or it has expired."""
        raise NotImplementedError

    @abstractmethod
    async def tokens(self) -> list[ApiToken]:
        """Every token, expired ones included, by name."""
        raise NotImplementedError

    @abstractmethod
    async def delete_token(self, name: str) -> bool:
        """Delete the token called ``name``; whether there was one."""
        raise NotImplementedError

    @abstractmethod
    async def add_session(
        self, secret_hash: str, principal: Principal, *, seconds: float
    ) -> None:
        """Store a session for ``principal``, expiring ``seconds`` from now."""
        raise NotImplementedError

    @abstractmethod
    async def session_by_hash(self, secret_hash: str) -> Principal | None:
        """The principal of the session with that hash, unless none or expired."""
        raise NotImplementedError

    @abstractmethod
    async def delete_session(self, secret_hash: str) -> bool:
        """Delete the session with that hash; whether there was one."""
        raise NotImplementedError

    @abstractmethod
    async def delete_sessions(self) -> int:
        """Delete every session; how many there were, expired ones included."""
        raise NotImplementedError

    @abstractmethod
    async def add_login(
        self, state_hash: str, login: PendingLogin, *, seconds: float
    ) -> None:
        """Store a sign-in in progress, expiring ``seconds`` from now."""
        raise NotImplementedError

    @abstractmethod
    async def take_login(self, state_hash: str) -> PendingLogin | None:
        """Remove the sign-in with that hash and return it, unless none or expired.

        Reading and removing are one step: of two takes at once, one gets it.
        """
        raise NotImplementedError

    @abstractmethod
    async def delete_expired(self) -> None:
        """Delete every expired session and sign-in.

        Expired tokens stay, so ``tokens`` still lists them until revoked.
        """
        raise NotImplementedError
