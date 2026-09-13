# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The task store in a dictionary, with the same lease rules as Postgres."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from neorc_core._errors import TaskNotFoundError, TaskStateError
from neorc_core._task import (
    LEASED_STATUSES,
    Payload,
    Task,
    TaskId,
    TaskStatus,
    ensure_transition,
)
from neorc_core.ports._queue_client import DEFAULT_LEASE_SECONDS
from neorc_core.ports._task_store import TaskStore


class MemoryTaskStore(TaskStore):
    """A task store in a dictionary, with the same lease rules as Postgres."""

    def __init__(self) -> None:
        self._tasks: dict[TaskId, Task] = {}
        self._lock = asyncio.Lock()

    async def add(
        self,
        name: str,
        payload: Payload,
        *,
        run_after: datetime | None = None,
        priority: int = 0,
    ) -> Task:
        now = datetime.now(UTC)
        task = Task(
            id=uuid.uuid4(),
            name=name,
            payload=dict(payload),
            status=TaskStatus.PENDING,
            created_at=now,
            run_after=run_after if run_after is not None else now,
            priority=priority,
        )
        async with self._lock:
            self._tasks[task.id] = task
        return task

    async def claim_next(
        self, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> Task | None:
        now = datetime.now(UTC)
        async with self._lock:
            ready = [task for task in self._tasks.values() if _is_claimable(task, now)]
            if not ready:
                return None
            ready.sort(key=lambda task: (-task.priority, task.run_after))
            claimed = replace(
                ready[0],
                status=TaskStatus.CLAIMED,
                attempts=ready[0].attempts + 1,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
            self._tasks[claimed.id] = claimed
            return claimed

    async def mark_started(self, task_id: TaskId) -> None:
        async with self._lock:
            task = self._require(task_id)
            ensure_transition(task.status, TaskStatus.RUNNING)
            self._tasks[task_id] = replace(task, status=TaskStatus.RUNNING)

    async def mark_finished(self, task_id: TaskId, *, error: str | None = None) -> None:
        status = TaskStatus.FAILED if error is not None else TaskStatus.SUCCEEDED
        async with self._lock:
            task = self._require(task_id)
            ensure_transition(task.status, status)
            self._tasks[task_id] = replace(
                task, status=status, error=error, lease_expires_at=None
            )

    async def extend_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        async with self._lock:
            task = self._require(task_id)
            if task.status not in LEASED_STATUSES:
                raise TaskStateError(
                    f"a {task.status.value} task holds no lease to extend"
                )
            expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
            self._tasks[task_id] = replace(task, lease_expires_at=expires_at)
            return expires_at

    async def get(self, task_id: TaskId) -> Task:
        async with self._lock:
            return self._require(task_id)

    def _require(self, task_id: TaskId) -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        return task


def _is_claimable(task: Task, now: datetime) -> bool:
    """Ready to run, and either never claimed or holding a lapsed lease."""
    if task.run_after > now:
        return False
    if task.status is TaskStatus.PENDING:
        return True
    return (
        task.status in LEASED_STATUSES
        and task.lease_expires_at is not None
        and task.lease_expires_at <= now
    )
