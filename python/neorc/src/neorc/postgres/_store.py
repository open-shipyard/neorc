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

import uuid
from datetime import datetime
from types import TracebackType

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from neorc.postgres._schema import TASKS_TABLE
from neorc_core import (
    LEASED_STATUSES,
    NeorcError,
    Payload,
    Task,
    TaskId,
    TaskNotFoundError,
    TaskStateError,
    TaskStatus,
    TaskStore,
    ensure_transition,
)
from neorc_core.ports._queue_client import DEFAULT_LEASE_SECONDS

_COLUMNS = (
    "id, name, payload, status, created_at, run_after, "
    "priority, attempts, lease_expires_at, error"
)

_INSERT = f"""
INSERT INTO {TASKS_TABLE} (id, name, payload, status, run_after, priority)
VALUES (%(id)s, %(name)s, %(payload)s, 'pending',
        COALESCE(%(run_after)s, now()), %(priority)s)
RETURNING {_COLUMNS}
"""

# One statement, so the claim and everything that goes with it are atomic. The
# inner SELECT picks the best claimable row and locks it, skipping rows another
# claimer already holds; a row is claimable when it is due and either untouched
# or holding a lease that has run out.
_CLAIM = f"""
UPDATE {TASKS_TABLE} AS t
   SET status = 'claimed',
       attempts = t.attempts + 1,
       lease_expires_at = now() + make_interval(secs => %(lease_seconds)s)
 WHERE t.id = (
       SELECT c.id
         FROM {TASKS_TABLE} AS c
        WHERE c.run_after <= now()
          AND (c.status = 'pending'
               OR (c.status IN ('claimed', 'running')
                   AND c.lease_expires_at <= now()))
        ORDER BY c.priority DESC, c.run_after
          FOR UPDATE SKIP LOCKED
        LIMIT 1)
RETURNING {_COLUMNS}
"""

_SELECT_FOR_UPDATE = f"SELECT {_COLUMNS} FROM {TASKS_TABLE} WHERE id = %s FOR UPDATE"

_SELECT = f"SELECT {_COLUMNS} FROM {TASKS_TABLE} WHERE id = %s"

_SET_STATUS = f"UPDATE {TASKS_TABLE} SET status = %(status)s WHERE id = %(id)s"

_FINISH = f"""
UPDATE {TASKS_TABLE}
   SET status = %(status)s, error = %(error)s, lease_expires_at = NULL
 WHERE id = %(id)s
"""

_EXTEND_LEASE = f"""
UPDATE {TASKS_TABLE}
   SET lease_expires_at = now() + make_interval(secs => %(lease_seconds)s)
 WHERE id = %(id)s
RETURNING lease_expires_at
"""


class PostgresTaskStore(TaskStore):
    """Stores tasks in Postgres, over a connection pool."""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: AsyncConnectionPool[AsyncConnection[DictRow]] | None = None

    async def __aenter__(self) -> PostgresTaskStore:
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
        pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
            self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
            kwargs={"row_factory": dict_row},
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
    def pool(self) -> AsyncConnectionPool[AsyncConnection[DictRow]]:
        """The open pool, for callers that share this store's connections."""
        if self._pool is None:
            raise NeorcError("the task store is not open")
        return self._pool

    async def add(
        self,
        name: str,
        payload: Payload,
        *,
        run_after: datetime | None = None,
        priority: int = 0,
    ) -> Task:
        params = {
            "id": uuid.uuid4(),
            "name": name,
            "payload": Jsonb(dict(payload)),
            "run_after": run_after,
            "priority": priority,
        }
        async with self.pool.connection() as conn:
            cursor = await conn.execute(_INSERT, params)
            row = await cursor.fetchone()
        if row is None:  # an INSERT ... RETURNING that returns nothing
            raise NeorcError("the task could not be stored")
        return _to_task(row)

    async def claim_next(
        self, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> Task | None:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(_CLAIM, {"lease_seconds": lease_seconds})
            row = await cursor.fetchone()
        return None if row is None else _to_task(row)

    async def mark_started(self, task_id: TaskId) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            task = await self._locked(conn, task_id)
            ensure_transition(task.status, TaskStatus.RUNNING)
            await conn.execute(
                _SET_STATUS, {"id": task_id, "status": TaskStatus.RUNNING.value}
            )

    async def mark_finished(self, task_id: TaskId, *, error: str | None = None) -> None:
        status = TaskStatus.FAILED if error is not None else TaskStatus.SUCCEEDED
        async with self.pool.connection() as conn, conn.transaction():
            task = await self._locked(conn, task_id)
            ensure_transition(task.status, status)
            await conn.execute(
                _FINISH, {"id": task_id, "status": status.value, "error": error}
            )

    async def extend_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        async with self.pool.connection() as conn, conn.transaction():
            task = await self._locked(conn, task_id)
            if task.status not in LEASED_STATUSES:
                raise TaskStateError(
                    f"a {task.status.value} task holds no lease to extend"
                )
            cursor = await conn.execute(
                _EXTEND_LEASE, {"id": task_id, "lease_seconds": lease_seconds}
            )
            row = await cursor.fetchone()
        if row is None:  # unreachable: the row was locked just above
            raise TaskNotFoundError(str(task_id))
        expires_at: datetime = row["lease_expires_at"]
        return expires_at

    async def get(self, task_id: TaskId) -> Task:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(_SELECT, (task_id,))
            row = await cursor.fetchone()
        if row is None:
            raise TaskNotFoundError(str(task_id))
        return _to_task(row)

    async def _locked(self, conn: AsyncConnection[DictRow], task_id: TaskId) -> Task:
        """Read a task with its row locked, so the caller can decide and write."""
        cursor = await conn.execute(_SELECT_FOR_UPDATE, (task_id,))
        row = await cursor.fetchone()
        if row is None:
            raise TaskNotFoundError(str(task_id))
        return _to_task(row)


def _to_task(row: DictRow) -> Task:
    return Task(
        id=row["id"],
        name=row["name"],
        payload=row["payload"],
        status=TaskStatus(row["status"]),
        created_at=row["created_at"],
        run_after=row["run_after"],
        priority=row["priority"],
        attempts=row["attempts"],
        lease_expires_at=row["lease_expires_at"],
        error=row["error"],
    )
