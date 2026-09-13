# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""What the manager service does, with no HTTP framework and no database in sight.

``neorc.manager`` exposes this over FastAPI; the storage it is handed comes from
whichever adapter the deployment installed.
"""

from __future__ import annotations

import time
from datetime import datetime

from neorc_core._task import Payload, Task, TaskId, TaskStatus
from neorc_core.ports._queue_client import DEFAULT_LEASE_SECONDS
from neorc_core.ports._task_notifier import TaskNotifier
from neorc_core.ports._task_store import TaskStore


class Manager:
    """Serves publishers and workers against a task store."""

    def __init__(self, store: TaskStore, notifier: TaskNotifier) -> None:
        self._store = store
        self._notifier = notifier

    async def publish(
        self,
        name: str,
        payload: Payload,
        *,
        run_after: datetime | None = None,
        priority: int = 0,
    ) -> Task:
        """Store a task and wake a waiting worker."""
        task = await self._store.add(
            name, payload, run_after=run_after, priority=priority
        )
        await self._notifier.notify()
        return task

    async def pick_next_task(
        self, *, timeout: float, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> Task | None:
        """Lease a task to a worker, waiting up to ``timeout`` seconds for one.

        Subscribes before the first claim, so a task published while this call
        is between a claim and a wait is not slept through. A waiting caller
        holds no store connection: it holds an event, and the store is only
        touched when there is reason to think a task is there.
        """
        deadline = time.monotonic() + timeout
        async with self._notifier.subscribe() as subscription:
            while True:
                task = await self._store.claim_next(lease_seconds=lease_seconds)
                if task is not None:
                    return task
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                # A wakeup is a hint; if the claim below finds nothing we come
                # back here and wait out the rest of the deadline.
                await subscription.wait(timeout=remaining)

    async def report_started(self, task_id: TaskId) -> None:
        """Record that a claimed task is now executing."""
        await self._store.mark_started(task_id)

    async def report_finished(
        self, task_id: TaskId, *, error: str | None = None
    ) -> None:
        """Record how a task ended."""
        await self._store.mark_finished(task_id, error=error)

    async def extend_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        """Take a worker's heartbeat and return the lease's new expiry."""
        return await self._store.extend_lease(task_id, lease_seconds=lease_seconds)

    async def get_task(self, task_id: TaskId) -> Task:
        """Return a task for a status query."""
        return await self._store.get(task_id)

    async def get_status(self, task_id: TaskId) -> TaskStatus:
        """Return just the status of a task."""
        task = await self._store.get(task_id)
        return task.status
