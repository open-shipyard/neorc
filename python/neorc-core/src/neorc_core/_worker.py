# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The worker loop: claim a task, run it, report what happened."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from neorc_core._task import Task
from neorc_core.ports._queue_client import DEFAULT_LEASE_SECONDS, QueueClient

TaskHandler = Callable[[Task], Awaitable[None]]

DEFAULT_POLL_TIMEOUT = 30.0

HEARTBEAT_FRACTION = 1 / 3
"""Heartbeat this far into the lease, so one missed beat does not lose the task."""


class Worker:
    """Runs tasks handed to it by a queue client.

    The queue client is injected, so a worker does not know or care whether the
    tasks reach it over HTTP, Redis or SQS.
    """

    def __init__(
        self,
        manager_address: str,
        queue_client: QueueClient,
        *,
        poll_timeout: float = DEFAULT_POLL_TIMEOUT,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> None:
        self._manager_address = manager_address
        self._queue_client = queue_client
        self._poll_timeout = poll_timeout
        self._lease_seconds = lease_seconds

    def register(self, name: str, handler: TaskHandler) -> None:
        """Bind a task name to the coroutine that executes it."""
        raise NotImplementedError

    def start(self) -> None:
        """Run the worker until it is stopped. Blocks the calling thread."""
        raise NotImplementedError

    def stop(self) -> None:
        """Ask a running worker to finish its current task and return."""
        raise NotImplementedError

    async def run(self) -> None:
        """The worker loop, for callers that already have an event loop."""
        raise NotImplementedError

    async def run_once(self) -> Task | None:
        """Claim and execute at most one task; return it, or ``None`` if idle."""
        raise NotImplementedError

    async def execute(self, task: Task) -> None:
        """Report the task started, dispatch it to its handler, report the outcome.

        Heartbeats for as long as the handler runs; a worker that dies here
        stops beating and the task returns to the queue when its lease lapses.
        """
        raise NotImplementedError

    async def heartbeat(self, task: Task) -> None:
        """Extend the task's lease every ``heartbeat_interval`` until cancelled."""
        raise NotImplementedError

    @property
    def heartbeat_interval(self) -> float:
        """How often to beat while a task runs, derived from the lease length."""
        raise NotImplementedError
