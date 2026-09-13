# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The worker for flows: check its queue's handlers, then take and run tasks.

Handlers are plain functions named by import path, called with a task's inputs
as keyword arguments; they need not import neorc. ``Worker`` keeps serving the
task API next to this.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from neorc_core import _values
from neorc_core._errors import InvalidValueError, NeorcError, RunStateError
from neorc_core._handlers import resolve_handler, signature_problems
from neorc_core._runs import TaskDelivery
from neorc_core._task import TaskId
from neorc_core.flows import DEFAULT_QUEUE, Namespace, TaskStep
from neorc_core.ports._flow_clients import FlowQueueClient
from neorc_core.ports._queue_client import DEFAULT_LEASE_SECONDS

DEFAULT_POLL_TIMEOUT = 30.0

HEARTBEAT_FRACTION = 1 / 3
"""Heartbeat this far into the lease, so one missed beat does not lose the task."""

_log = logging.getLogger(__name__)


class HandlerError(NeorcError):
    """A worker's handlers do not fit its queue's tasks. ``problems`` lists why."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("handlers do not fit their tasks:\n" + "\n".join(problems))


class FlowWorker:
    """Takes tasks from one queue and runs their handlers.

    Call ``prepare`` before ``run`` or ``run_once``: it checks every handler on
    the queue imports and takes exactly its task's inputs.
    """

    def __init__(
        self,
        client: FlowQueueClient,
        *,
        queue: str = DEFAULT_QUEUE,
        code_location: Path | None = None,
        poll_timeout: float = DEFAULT_POLL_TIMEOUT,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ) -> None:
        self._client = client
        self._queue = queue
        self._code_location = code_location
        self._poll_timeout = poll_timeout
        self._lease_seconds = lease_seconds
        self._handlers: dict[str, Callable[..., Any]] = {}
        self._stopping = False
        self._polling: asyncio.Task[TaskDelivery | None] | None = None

    @property
    def queue(self) -> str:
        return self._queue

    async def prepare(self) -> None:
        """Pull the queue's task definitions and check every handler against them.

        Raises ``HandlerError`` listing every handler that does not import, or
        whose parameters do not match its task's ``params`` and ``fixed_params``.
        """
        problems: list[str] = []
        for task in await self._client.task_definitions(self._queue):
            try:
                function = self._resolve(task.handler)
            except ValueError as exc:
                problems.append(f"{task.name}: {exc}")
                continue
            problems.extend(
                f"{task.name}: {task.handler} {problem}"
                for problem in signature_problems(function, _input_names(task))
            )
        if problems:
            raise HandlerError(problems)

    def stop(self) -> None:
        """Finish the task in hand, if any, and return; cut an idle poll short."""
        self._stopping = True
        polling = self._polling
        if polling is not None and not polling.done():
            polling.cancel()

    async def run(self) -> None:
        """Take and run tasks until ``stop`` is called."""
        _log.info("worker started on queue %r", self._queue)
        while not self._stopping:
            try:
                await self.run_once()
            except NeorcError:
                _log.exception("worker iteration failed")
                await asyncio.sleep(1)
        _log.info("worker stopped")

    async def run_once(self) -> TaskDelivery | None:
        """Take and run at most one task; return it, or ``None`` if idle."""
        polling = asyncio.ensure_future(
            self._client.pick_next_task(
                self._queue,
                timeout=self._poll_timeout,
                lease_seconds=self._lease_seconds,
            )
        )
        self._polling = polling
        try:
            delivery = await polling
        except asyncio.CancelledError:
            if self._stopping:
                return None
            raise
        finally:
            self._polling = None
        if delivery is None:
            return None
        await self.execute(delivery)
        return delivery

    async def execute(self, delivery: TaskDelivery) -> None:
        """Report the start, call the handler, report its result or failure.

        A task whose run is no longer active is dropped without running it.
        """
        task = delivery.task
        try:
            await self._client.report_started(task.id)
        except RunStateError:
            _log.info("dropping task %s: its run is no longer active", task.id)
            return

        heartbeat = asyncio.create_task(self._heartbeat(task.id))
        result: _values.JsonValue = None
        error: str | None = None
        try:
            function = self._resolve(task.handler)
            arguments = _values.decode(dict(delivery.inputs))
            for name, reference in task.params.items():
                if (
                    reference.namespace is Namespace.NEORC
                    and reference.name == "attempts"
                ):
                    arguments[name] = task.attempts
            if inspect.iscoroutinefunction(function):
                value = await function(**arguments)
            else:
                value = await asyncio.to_thread(function, **arguments)
        except asyncio.CancelledError:
            # Let the lease lapse: the task belongs to whoever takes it next.
            raise
        except BaseException as exc:  # SystemExit from a handler fails its task too
            _log.exception("task %s failed", task.id)
            error = f"{type(exc).__name__}: {exc}"
        else:
            try:
                result = _values.encode(value)
            except (InvalidValueError, RecursionError) as exc:
                error = f"invalid result: {type(exc).__name__}: {exc}"
        finally:
            heartbeat.cancel()
        await self._client.report_finished(task.id, result=result, error=error)

    def _resolve(self, handler: str) -> Callable[..., Any]:
        function = self._handlers.get(handler)
        if function is None:
            function = resolve_handler(handler, self._code_location)
            self._handlers[handler] = function
        return function

    async def _heartbeat(self, task_id: TaskId) -> None:
        while True:
            await asyncio.sleep(self._lease_seconds * HEARTBEAT_FRACTION)
            try:
                await self._client.extend_lease(
                    task_id, lease_seconds=self._lease_seconds
                )
            except Exception:
                # Keep beating: one failed call should not cost the task.
                _log.warning("heartbeat for task %s failed", task_id, exc_info=True)


def _input_names(task: TaskStep) -> set[str]:
    return set(task.params) | set(task.fixed_params)
