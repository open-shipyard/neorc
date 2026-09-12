# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The queue client publishers and workers use to reach the manager over HTTP."""

from __future__ import annotations

from datetime import datetime
from types import TracebackType

import httpx

from neorc_core import Payload, QueueClient, Task, TaskId, TaskStatus
from neorc_core.ports._queue_client import DEFAULT_LEASE_SECONDS

DEFAULT_POLL_TIMEOUT = 30.0


class HttpQueueClient(QueueClient):
    """Talks to the manager service over HTTP/JSON.

    ``pick_next_task`` long-polls: the request stays open until a task is ready
    or the manager gives up, whichever comes first. The client-side timeout must
    outlast the manager's own deadline.
    """

    def __init__(
        self,
        manager_address: str,
        *,
        poll_timeout: float = DEFAULT_POLL_TIMEOUT,
    ) -> None:
        self._manager_address = manager_address
        self._poll_timeout = poll_timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> HttpQueueClient:
        raise NotImplementedError

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release the underlying connection pool."""
        raise NotImplementedError

    async def publish(
        self,
        name: str,
        payload: Payload,
        *,
        run_after: datetime | None = None,
        priority: int = 0,
    ) -> TaskId:
        raise NotImplementedError

    async def pick_next_task(
        self, *, timeout: float, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> Task | None:
        raise NotImplementedError

    async def extend_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        raise NotImplementedError

    async def report_started(self, task_id: TaskId) -> None:
        raise NotImplementedError

    async def report_finished(
        self, task_id: TaskId, *, error: str | None = None
    ) -> None:
        raise NotImplementedError

    async def get_status(self, task_id: TaskId) -> TaskStatus:
        raise NotImplementedError
