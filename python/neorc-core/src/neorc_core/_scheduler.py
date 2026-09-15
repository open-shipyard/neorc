# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The scheduler: I/O around the planner.

It waits for events, reads the state of each run they concern and the
definition of that run's version, calls ``plan``, and applies the actions
through a manager client. It keeps no state of its own beyond where it is in
the event log, so a restarted scheduler that reads events again plans nothing
new.

A refused token is neither a rejected request nor a passing failure: it fails
no run, and ends ``run`` with the refusal, since asking again would be refused
again.
"""

from __future__ import annotations

import asyncio
import logging

from neorc_core._errors import (
    AuthenticationError,
    ManagerUnavailableError,
    NeorcError,
)
from neorc_core._planner import (
    Action,
    FailRun,
    PublishTask,
    StartSubRun,
    SucceedRun,
    plan,
)
from neorc_core._runs import Event, EventKind, Run, RunId, RunStatus
from neorc_core.flows import FlowDefinition, Version
from neorc_core.ports._clients import ManagerClient

DEFAULT_POLL_TIMEOUT = 30.0

_log = logging.getLogger(__name__)


class Scheduler:
    """Moves runs forward as their events arrive."""

    def __init__(
        self, client: ManagerClient, *, poll_timeout: float = DEFAULT_POLL_TIMEOUT
    ) -> None:
        self._client = client
        self._poll_timeout = poll_timeout
        self._after = 0
        self._definitions: dict[tuple[str, Version], FlowDefinition] = {}
        self._stopping = False
        self._polling: asyncio.Task[list[Event]] | None = None

    def stop(self) -> None:
        """Return from ``run`` after the events in hand; cut an idle poll short."""
        self._stopping = True
        polling = self._polling
        if polling is not None and not polling.done():
            polling.cancel()

    async def run(self) -> None:
        """Handle events until ``stop`` is called, or the manager refuses the token."""
        _log.info("scheduler started")
        while not self._stopping:
            try:
                await self.run_once()
            except AuthenticationError:
                _log.error("the manager refused the scheduler's API token")
                raise
            except NeorcError:
                _log.exception("scheduler iteration failed")
                await asyncio.sleep(1)
        _log.info("scheduler stopped")

    async def run_once(self) -> list[Event]:
        """Wait for the next events and move every run they concern forward."""
        polling = asyncio.ensure_future(
            self._client.wait_for_events(self._after, timeout=self._poll_timeout)
        )
        self._polling = polling
        try:
            events = await polling
        except asyncio.CancelledError:
            if self._stopping:
                return []
            raise
        finally:
            self._polling = None

        runs: list[RunId] = []
        for event in events:
            for run_id in await self._runs_to_advance(event):
                if run_id not in runs:
                    runs.append(run_id)
        for run_id in runs:
            await self.advance(run_id)
        if events:
            self._after = events[-1].sequence
        return events

    async def advance(self, run_id: RunId) -> None:
        """Plan an active run from its state and apply the actions."""
        run = await self._client.get_run(run_id)
        if run.status is not RunStatus.ACTIVE:
            return
        definition = await self._definition(run)
        state = await self._client.run_state(run_id)
        for action in plan(definition, state):
            try:
                await self._apply(run, action)
            except (ManagerUnavailableError, AuthenticationError):
                raise
            except NeorcError as exc:
                await self._rejected(run, action, exc)
                return

    async def _runs_to_advance(self, event: Event) -> list[RunId]:
        """The run an event is about, and the parent waiting on it once it finished."""
        if event.kind is not EventKind.RUN_FINISHED:
            return [event.run_id]
        run = await self._client.get_run(event.run_id)
        return [run.parent_id] if run.parent_id is not None else []

    async def _apply(self, run: Run, action: Action) -> None:
        if isinstance(action, PublishTask):
            await self._client.publish_task(run.id, action.address)
        elif isinstance(action, StartSubRun):
            await self._client.start_sub_run(run.id, action.address)
        elif isinstance(action, SucceedRun):
            await self._client.succeed_run(run.id, action.output)
        elif isinstance(action, FailRun):
            await self._client.fail_run(run.id, action.reason)

    async def _rejected(self, run: Run, action: Action, exc: NeorcError) -> None:
        """A rejected request fails the run, unless the run has already finished."""
        current = await self._client.get_run(run.id)
        if current.status is not RunStatus.ACTIVE:
            return
        where = getattr(action, "address", None)
        prefix = f"{where}: " if where is not None else ""
        reason = f"{prefix}{type(exc).__name__}: {exc}"
        _log.info("run %s fails: %s", run.id, reason)
        await self._client.fail_run(run.id, reason)

    async def _definition(self, run: Run) -> FlowDefinition:
        key = (run.flow, run.version)
        definition = self._definitions.get(key)
        if definition is None:
            definition = await self._client.get_flow(run.flow, run.version)
            self._definitions[key] = definition
        return definition
