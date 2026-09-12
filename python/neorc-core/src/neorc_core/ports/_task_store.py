# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The port the manager uses to persist tasks. Implemented by ``neorc.postgres``."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from neorc_core._task import Payload, Task, TaskId
from neorc_core.ports._queue_client import DEFAULT_LEASE_SECONDS


class TaskStore(ABC):
    """Durable storage for tasks and their state."""

    @abstractmethod
    async def add(
        self,
        name: str,
        payload: Payload,
        *,
        run_after: datetime | None = None,
        priority: int = 0,
    ) -> Task:
        """Record a new pending task."""
        raise NotImplementedError

    @abstractmethod
    async def claim_next(
        self, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> Task | None:
        """Atomically lease the highest-priority ready task, or return ``None``.

        Ready means due, and either never claimed or holding a lapsed lease. No
        two concurrent callers may be given the same task; taking the lease,
        changing the status and incrementing the attempt count are one
        indivisible step.
        """
        raise NotImplementedError

    @abstractmethod
    async def extend_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        """Push a task's lease out from now, and return its new expiry."""
        raise NotImplementedError

    @abstractmethod
    async def mark_started(self, task_id: TaskId) -> None:
        """Record that a worker began executing a claimed task."""
        raise NotImplementedError

    @abstractmethod
    async def mark_finished(self, task_id: TaskId, *, error: str | None = None) -> None:
        """Record the outcome of a task, successful unless ``error`` is set."""
        raise NotImplementedError

    @abstractmethod
    async def get(self, task_id: TaskId) -> Task:
        """Return a task, raising ``TaskNotFoundError`` if it does not exist."""
        raise NotImplementedError
