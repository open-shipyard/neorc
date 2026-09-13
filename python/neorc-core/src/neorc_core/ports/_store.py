# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The single port a manager keeps flows, runs, tasks and events behind.

Each method is one atomic operation: a transaction in a database, one lock in
memory. Core never composes several of them into something that has to be
atomic. The decisions an operation makes inside, such as the upload rules and a
run's state, come from ``neorc_core._runs``, so every store makes them alike.

This port is for flows. ``TaskStore`` keeps serving the task API until the
deployed adapters move here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import datetime

from neorc_core._runs import Event, FlowTask, Run, RunId, StoredFlow
from neorc_core._task import TaskId
from neorc_core._values import JsonValue
from neorc_core.flows import Address, FlowDefinition, Reference, RunState, Version
from neorc_core.ports._queue_client import DEFAULT_LEASE_SECONDS


class Store(ABC):
    """Durable storage for flow versions, runs, their tasks and their events."""

    @abstractmethod
    async def store_flow(self, definition: FlowDefinition, content: JsonValue) -> bool:
        """Store a flow version, and cancel the run trees it replaces.

        ``check_upload`` decides against the flow's stored versions: ``False``
        means an identical upload, and nothing changes. A stored new version
        cancels, in the same operation, every active run tree that contains a
        run of this flow. Raises ``FlowVersionError`` for a rejected upload.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_flow(self, name: str, version: Version | None = None) -> StoredFlow:
        """A flow version, the latest when ``version`` is ``None``.

        Raises ``FlowNotFoundError`` if there is no such flow or version.
        """
        raise NotImplementedError

    @abstractmethod
    async def latest_flows(self) -> list[StoredFlow]:
        """The latest version of every stored flow, by name."""
        raise NotImplementedError

    @abstractmethod
    async def start_run(
        self,
        flow: str,
        version: Version,
        inputs: Mapping[str, JsonValue],
        *,
        parent_id: RunId | None = None,
        parent_address: Address | None = None,
    ) -> Run:
        """Start an active run of ``version``, and record a "run started" event.

        ``version`` must still be the flow's latest, or this raises
        ``FlowVersionError``; ``FlowNotFoundError`` if the flow is unknown. A
        sub-flow run names its parent and the step instance it is: the parent
        must be active (``RunStateError``), and starting the same instance again
        returns the run already started, with no new event.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_run(self, run_id: RunId) -> Run:
        """A run; ``RunNotFoundError`` if there is none."""
        raise NotImplementedError

    @abstractmethod
    async def run_state(self, run_id: RunId) -> RunState:
        """The run's state, built by ``run_state_of`` from its tasks and sub-runs."""
        raise NotImplementedError

    @abstractmethod
    async def succeed_run(self, run_id: RunId, output: JsonValue) -> Run:
        """Mark an active run succeeded with ``output``; record "run finished".

        Raises ``RunStateError`` if the run is not active.
        """
        raise NotImplementedError

    @abstractmethod
    async def fail_run_tree(self, run_id: RunId, reason: str) -> None:
        """Fail every active run in ``run_id``'s tree, recording "run finished"."""
        raise NotImplementedError

    @abstractmethod
    async def cancel_run_tree(self, run_id: RunId, reason: str) -> None:
        """Cancel every active run in ``run_id``'s tree, recording "run finished"."""
        raise NotImplementedError

    @abstractmethod
    async def publish_task(
        self,
        run_id: RunId,
        address: Address,
        *,
        queue: str,
        handler: str,
        params: Mapping[str, Reference],
        fixed_params: Mapping[str, JsonValue],
    ) -> FlowTask:
        """Publish the pending task at ``address`` in an active run.

        Its id is ``task_id_for(run_id, address)``. Publishing an address again
        returns the task already there, unchanged. Raises ``RunStateError`` if
        the run is not active.
        """
        raise NotImplementedError

    @abstractmethod
    async def claim_task(
        self, queue: str, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> FlowTask | None:
        """Lease the oldest ready task on ``queue``, or return ``None``.

        Ready means pending, or holding a lapsed lease. Taking the lease,
        changing the status and counting the attempt are one step, and no two
        callers are given the same task.
        """
        raise NotImplementedError

    @abstractmethod
    async def start_task(self, task_id: TaskId) -> FlowTask:
        """Record that a worker began executing a claimed task.

        If the task's run is no longer active, the task fails instead, so it is
        never handed out again, and this raises ``RunStateError``: the worker
        drops it without running it.
        """
        raise NotImplementedError

    @abstractmethod
    async def extend_task_lease(
        self, task_id: TaskId, *, lease_seconds: float = DEFAULT_LEASE_SECONDS
    ) -> datetime:
        """Push a leased task's lease out from now, and return its new expiry."""
        raise NotImplementedError

    @abstractmethod
    async def finish_task(
        self, task_id: TaskId, *, result: JsonValue = None, error: str | None = None
    ) -> FlowTask:
        """Record a task's result, or its failure when ``error`` is set.

        Records "task finished" whether or not the run is still active: a task
        already in flight may finish after its run was cancelled.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_task(self, task_id: TaskId) -> FlowTask:
        """A task; ``TaskNotFoundError`` if there is none."""
        raise NotImplementedError

    @abstractmethod
    async def events_after(self, sequence: int, *, limit: int = 100) -> list[Event]:
        """Up to ``limit`` events with a sequence above ``sequence``, oldest first."""
        raise NotImplementedError
