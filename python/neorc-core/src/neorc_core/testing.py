# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""In-memory adapters, for testing handlers and manager behaviour without a database.

These keep everything in one process: useful in tests, useless in a deployment,
since nothing here survives a restart or is seen by another process.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from neorc_core._errors import TaskNotFoundError, TaskStateError
from neorc_core._manager import Manager
from neorc_core._subscriptions import LocalSubscriptions
from neorc_core._task import (
    LEASED_STATUSES,
    Payload,
    Task,
    TaskId,
    TaskStatus,
    ensure_transition,
)
from neorc_core.ports._queue_client import DEFAULT_LEASE_SECONDS, QueueClient
from neorc_core.ports._task_notifier import Subscription, TaskNotifier
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


class MemoryTaskNotifier(TaskNotifier):
    """Wakes waiters in this process only."""

    def __init__(self) -> None:
        self._subscriptions = LocalSubscriptions()

    async def notify(self) -> None:
        self._subscriptions.wake_all()

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[Subscription]:
        async with self._subscriptions.subscribe() as subscription:
            yield subscription


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


class DirectQueueClient(QueueClient):
    """A queue client that calls a manager in this process, with no HTTP in between.

    Lets a worker and its handlers be exercised end to end without a running
    manager service.
    """

    def __init__(self, manager: Manager) -> None:
        self._manager = manager

    async def publish(
        self,
        name: str,
        payload: Payload,
        *,
        run_after: datetime | None = None,
        priority: int = 0,
    ) -> TaskId:
        task = await self._manager.publish(
            name, payload, run_after=run_after, priority=priority
        )
        return task.id

    async def pick_next_task(
        self, *, timeout: float, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> Task | None:
        return await self._manager.pick_next_task(
            timeout=timeout, lease_seconds=lease_seconds
        )

    async def extend_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        return await self._manager.extend_lease(task_id, lease_seconds=lease_seconds)

    async def report_started(self, task_id: TaskId) -> None:
        await self._manager.report_started(task_id)

    async def report_finished(
        self, task_id: TaskId, *, error: str | None = None
    ) -> None:
        await self._manager.report_finished(task_id, error=error)

    async def get_status(self, task_id: TaskId) -> TaskStatus:
        return await self._manager.get_status(task_id)
