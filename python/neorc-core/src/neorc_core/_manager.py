# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""What the manager service does, with no HTTP framework and no database in sight.

``neorc.manager`` exposes this over FastAPI; the storage it is handed comes from
whichever adapter the deployment installed.
"""

from __future__ import annotations

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
        raise NotImplementedError

    async def pick_next_task(
        self, *, timeout: float, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> Task | None:
        """Lease a task to a worker, waiting up to ``timeout`` seconds for one.

        Waits on the notifier and claims from the store; a wakeup that yields
        nothing goes back to waiting until the deadline. A waiting caller must
        not tie up a store connection.
        """
        raise NotImplementedError

    async def extend_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        """Take a worker's heartbeat and return the lease's new expiry."""
        raise NotImplementedError

    async def report_started(self, task_id: TaskId) -> None:
        """Record that a claimed task is now executing."""
        raise NotImplementedError

    async def report_finished(
        self, task_id: TaskId, *, error: str | None = None
    ) -> None:
        """Record how a task ended."""
        raise NotImplementedError

    async def get_task(self, task_id: TaskId) -> Task:
        """Return a task for a status query."""
        raise NotImplementedError

    async def get_status(self, task_id: TaskId) -> TaskStatus:
        """Return just the status of a task."""
        raise NotImplementedError
