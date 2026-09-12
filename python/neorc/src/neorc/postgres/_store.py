# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The Postgres task store.

Claiming uses ``FOR UPDATE SKIP LOCKED`` so concurrent claimers step over rows
another transaction is already taking instead of queueing behind them. The claim
takes a lease, changes the status and bumps the attempt count in one
transaction; a task whose lease lapses is claimable again, which is how the work
of a dead worker comes back.
"""

from __future__ import annotations

from datetime import datetime
from types import TracebackType

from psycopg_pool import AsyncConnectionPool

from neorc_core import Payload, Task, TaskId, TaskStore
from neorc_core.ports._queue_client import DEFAULT_LEASE_SECONDS


class PostgresTaskStore(TaskStore):
    """Stores tasks in Postgres, over a connection pool."""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: AsyncConnectionPool | None = None

    async def __aenter__(self) -> PostgresTaskStore:
        raise NotImplementedError

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        raise NotImplementedError

    async def open(self) -> None:
        """Open the connection pool."""
        raise NotImplementedError

    async def aclose(self) -> None:
        """Close the connection pool."""
        raise NotImplementedError

    async def add(
        self,
        name: str,
        payload: Payload,
        *,
        run_after: datetime | None = None,
        priority: int = 0,
    ) -> Task:
        raise NotImplementedError

    async def claim_next(
        self, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> Task | None:
        raise NotImplementedError

    async def extend_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        raise NotImplementedError

    async def mark_started(self, task_id: TaskId) -> None:
        raise NotImplementedError

    async def mark_finished(self, task_id: TaskId, *, error: str | None = None) -> None:
        raise NotImplementedError

    async def get(self, task_id: TaskId) -> Task:
        raise NotImplementedError
