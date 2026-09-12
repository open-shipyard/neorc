# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The port publishers and workers speak to reach the queue.

The HTTP implementation in ``neorc`` long-polls the manager. Redis or SQS can
take its place without either side of this port changing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from neorc_core._task import Payload, Task, TaskId, TaskStatus

DEFAULT_LEASE_SECONDS = 60.0


class QueueClient(ABC):
    """Publish tasks, and claim the next one to run."""

    @abstractmethod
    async def publish(
        self,
        name: str,
        payload: Payload,
        *,
        run_after: datetime | None = None,
        priority: int = 0,
    ) -> TaskId:
        """Enqueue a task and return its id.

        ``run_after`` defers the task; ``None`` means it is ready immediately.
        """
        raise NotImplementedError

    @abstractmethod
    async def pick_next_task(
        self, *, timeout: float, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> Task | None:
        """Claim the next task to run, waiting up to ``timeout`` seconds.

        Returns ``None`` when the wait elapses with nothing to claim. The claim
        is a lease: it is exclusive for ``lease_seconds``, after which the task
        is claimable again unless the holder extends it. A task is handed to
        exactly one live worker.
        """
        raise NotImplementedError

    @abstractmethod
    async def extend_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        """Heartbeat: push the lease out, and return its new expiry.

        A worker calls this while it executes. Stopping is how a dead worker
        gives its task back. Maps to ``ChangeMessageVisibility`` on SQS.
        """
        raise NotImplementedError

    @abstractmethod
    async def report_started(self, task_id: TaskId) -> None:
        """Tell the manager the task is now being executed.

        Deliberately separate from claiming, so the two can be served by
        different backends.
        """
        raise NotImplementedError

    @abstractmethod
    async def report_finished(
        self, task_id: TaskId, *, error: str | None = None
    ) -> None:
        """Tell the manager the task ended, successfully unless ``error`` is set."""
        raise NotImplementedError

    @abstractmethod
    async def get_status(self, task_id: TaskId) -> TaskStatus:
        """Return the current status of a task, for publishers that follow up."""
        raise NotImplementedError
