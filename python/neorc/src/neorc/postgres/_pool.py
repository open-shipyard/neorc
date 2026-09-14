# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""A connection pool the Postgres stores share the handling of."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from psycopg import AsyncConnection, IsolationLevel
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from neorc_core import NeorcError

Pool = AsyncConnectionPool[AsyncConnection[DictRow]]


async def _read_committed(conn: AsyncConnection[DictRow]) -> None:
    # The stores' locking is reasoned out for READ COMMITTED: a statement after
    # a lock sees what was committed before the lock was granted. Pinned here,
    # so a server or role with another default does not change that.
    await conn.set_isolation_level(IsolationLevel.READ_COMMITTED)


class Pooled:
    """Owns a pool of connections handing rows over as dictionaries.

    Every connection runs its transactions in ``READ COMMITTED``, whatever the
    server's default.
    """

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: Pool | None = None

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def open(self) -> None:
        """Open the connection pool."""
        if self._pool is not None:
            return
        pool: Pool = AsyncConnectionPool(
            self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
            kwargs={"row_factory": dict_row},
            configure=_read_committed,
            open=False,
        )
        await pool.open(wait=True)
        self._pool = pool

    async def aclose(self) -> None:
        """Close the connection pool."""
        if self._pool is None:
            return
        await self._pool.close()
        self._pool = None

    @property
    def pool(self) -> Pool:
        """The open pool, for callers that share this store's connections."""
        if self._pool is None:
            raise NeorcError("the store is not open")
        return self._pool
