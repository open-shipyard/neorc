# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The worker loop: claim a task, run it, report what happened."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from neorc_core._errors import NeorcError
from neorc_core._task import Task
from neorc_core.ports._queue_client import DEFAULT_LEASE_SECONDS, QueueClient

TaskHandler = Callable[[Task], Awaitable[None]]

DEFAULT_POLL_TIMEOUT = 30.0

HEARTBEAT_FRACTION = 1 / 3
"""Heartbeat this far into the lease, so one missed beat does not lose the task."""

_log = logging.getLogger(__name__)


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
        self._handlers: dict[str, TaskHandler] = {}
        self._stopping = False
        self._polling: asyncio.Task[Task | None] | None = None

    def register(self, name: str, handler: TaskHandler) -> None:
        """Bind a task name to the coroutine that executes it."""
        if name in self._handlers:
            raise ValueError(f"a handler for {name!r} is already registered")
        self._handlers[name] = handler

    def start(self) -> None:
        """Run the worker until it is stopped. Blocks the calling thread."""
        asyncio.run(self.run())

    def stop(self) -> None:
        """Ask a running worker to finish its current task and return.

        A task being executed is always allowed to finish. A poll that is merely
        waiting for work is cut short, so a worker shuts down in the moment
        rather than at the end of its long poll — which is the difference
        between exiting and being killed when a supervisor's grace period runs
        out.

        Call it from the worker's own event loop or a signal handler on it.
        """
        self._stopping = True
        polling = self._polling
        if polling is not None and not polling.done():
            polling.cancel()

    async def run(self) -> None:
        """The worker loop, for callers that already have an event loop."""
        self._stopping = False
        _log.info("worker started against %s", self._manager_address)
        while not self._stopping:
            try:
                await self.run_once()
            except NeorcError:
                # The manager being unreachable is not fatal: workers outlive
                # manager restarts, and the lease gives the task back anyway.
                _log.exception("worker iteration failed")
                await asyncio.sleep(1)
        _log.info("worker stopped")

    async def run_once(self) -> Task | None:
        """Claim and execute at most one task; return it, or ``None`` if idle."""
        task = await self._pick()
        if task is None:
            return None
        await self.execute(task)
        return task

    async def _pick(self) -> Task | None:
        """Wait for a task, in a form ``stop`` can interrupt.

        Cancelling a poll can lose a task the manager had just handed over; its
        lease lapses and another worker takes it. That is the at-least-once
        bargain, and it beats hanging on to a shutdown for a whole poll.
        """
        polling = asyncio.ensure_future(
            self._queue_client.pick_next_task(
                timeout=self._poll_timeout, lease_seconds=self._lease_seconds
            )
        )
        self._polling = polling
        try:
            return await polling
        except asyncio.CancelledError:
            if self._stopping:
                return None
            raise
        finally:
            self._polling = None

    async def execute(self, task: Task) -> None:
        """Report the task started, dispatch it to its handler, report the outcome.

        Heartbeats for as long as the handler runs; a worker that dies here
        stops beating and the task returns to the queue when its lease lapses.
        """
        handler = self._handlers.get(task.name)
        if handler is None:
            await self._queue_client.report_finished(
                task.id, error=f"no handler registered for {task.name!r}"
            )
            return

        await self._queue_client.report_started(task.id)
        heartbeat = asyncio.create_task(self.heartbeat(task))
        error: str | None = None
        try:
            await handler(task)
        except asyncio.CancelledError:
            # Let the lease lapse rather than reporting an outcome we do not
            # know: the task belongs to whoever picks it up next.
            heartbeat.cancel()
            raise
        except Exception as exc:
            _log.exception("task %s failed", task.id)
            error = f"{type(exc).__name__}: {exc}"
        finally:
            heartbeat.cancel()
        await self._queue_client.report_finished(task.id, error=error)

    async def heartbeat(self, task: Task) -> None:
        """Extend the task's lease every ``heartbeat_interval`` until cancelled."""
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            try:
                await self._queue_client.extend_lease(
                    task.id, lease_seconds=self._lease_seconds
                )
            except NeorcError:
                # Keep beating: a manager that is briefly unreachable should not
                # cost the task, and if it stays unreachable the lease lapses.
                _log.warning("heartbeat for task %s failed", task.id, exc_info=True)

    @property
    def heartbeat_interval(self) -> float:
        """How often to beat while a task runs, derived from the lease length."""
        return self._lease_seconds * HEARTBEAT_FRACTION
