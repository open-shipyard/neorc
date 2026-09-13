# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The queue client that calls a manager in the same process."""

from __future__ import annotations

from datetime import datetime

from neorc_core._manager import Manager
from neorc_core._task import Payload, Task, TaskId, TaskStatus
from neorc_core.ports._queue_client import DEFAULT_LEASE_SECONDS, QueueClient


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
