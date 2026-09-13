# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Flow versions, runs, their tasks and events, and the rules every store applies.

The dataclasses are what a store keeps. The functions are the decisions a store
makes inside one atomic operation — whether an upload is stored, what a run's
state is — kept here so every store makes them the same way.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from neorc_core._errors import FlowVersionError, RunStateError
from neorc_core._task import TaskId, TaskStatus
from neorc_core._values import JsonValue
from neorc_core.flows import (
    Address,
    FlowDefinition,
    Outcome,
    Reference,
    RunState,
    StepResult,
    Version,
)

RunId = uuid.UUID


class RunStatus(StrEnum):
    """Where a run is. A run starts ``ACTIVE`` and ends in one of the others."""

    ACTIVE = "active"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class StoredFlow:
    """One version of a flow: its definition, and the structure it was uploaded as."""

    definition: FlowDefinition
    content: JsonValue

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def version(self) -> Version:
        return self.definition.version


@dataclass(frozen=True, slots=True)
class Run:
    """One execution of a flow version, top-level or a sub-flow of another run."""

    id: RunId
    flow: str
    version: Version
    inputs: Mapping[str, JsonValue]
    status: RunStatus
    root_id: RunId
    """The top-level run of this run's tree; its own id for a top-level run."""
    parent_id: RunId | None = None
    parent_address: Address | None = None
    """The sub-flow step instance, in the parent run, that this run is."""
    output: JsonValue = None
    """The flow's output, once the run succeeded with one."""
    reason: str | None = None
    """Why the run failed or was cancelled."""


@dataclass(frozen=True, slots=True)
class FlowTask:
    """A task published into a run, its inputs still references."""

    id: TaskId
    run_id: RunId
    address: Address
    queue: str
    handler: str
    params: Mapping[str, Reference] = field(default_factory=dict)
    fixed_params: Mapping[str, JsonValue] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = 0
    lease_expires_at: datetime | None = None
    result: JsonValue = None
    """The handler's return value, in its JSON form, once the task succeeded."""
    error: str | None = None


class EventKind(StrEnum):
    """What happened to a run, for the scheduler to act on."""

    RUN_STARTED = "run_started"
    TASK_FINISHED = "task_finished"
    RUN_FINISHED = "run_finished"


@dataclass(frozen=True, slots=True)
class Event:
    """Something the scheduler needs to look at a run for.

    ``sequence`` orders events and increases with every event a store appends.
    """

    sequence: int
    run_id: RunId
    kind: EventKind


def task_id_for(run_id: RunId, address: Address) -> TaskId:
    """The id of the task at ``address`` in a run: the same on every publish."""
    return uuid.uuid5(run_id, f"task:{address}")


def sub_run_id_for(parent_id: RunId, address: Address) -> RunId:
    """The id of the sub-flow run at ``address`` in a parent run."""
    return uuid.uuid5(parent_id, f"run:{address}")


def check_upload(
    stored: Iterable[StoredFlow], definition: FlowDefinition, content: JsonValue
) -> bool:
    """Whether an upload is a new version to store, given the flow's stored versions.

    ``False`` for a version already stored with identical content: nothing to do.
    Raises ``FlowVersionError`` for changed content on a stored version, and for
    a version lower than the highest stored one.
    """
    versions = {flow.version: flow for flow in stored}
    same = versions.get(definition.version)
    if same is not None:
        if _canonical(same.content) == _canonical(content):
            return False
        raise FlowVersionError(
            f"{definition.name} {definition.version} is already stored with "
            "different content: changed content needs a new version"
        )
    if versions and definition.version < max(versions):
        raise FlowVersionError(
            f"{definition.name} {definition.version} is lower than the stored "
            f"{max(versions)}: versions only move forward"
        )
    return True


def _canonical(content: JsonValue) -> str:
    """JSON text that is the same exactly when the structures are.

    Python's ``==`` holds ``1``, ``1.0`` and ``True`` equal; JSON does not.
    """
    return json.dumps(content, sort_keys=True, separators=(",", ":"))


def ensure_active(run: Run) -> None:
    """Raise ``RunStateError`` unless ``run`` is active."""
    if run.status is not RunStatus.ACTIVE:
        raise RunStateError(f"run {run.id} is {run.status.value}, no longer active")


def run_state_of(
    run: Run, tasks: Iterable[FlowTask], sub_runs: Iterable[Run]
) -> RunState:
    """A run's state, from its tasks and the sub-flow runs it started."""
    steps: dict[Address, StepResult] = {}
    for task in tasks:
        if task.status is TaskStatus.SUCCEEDED:
            steps[task.address] = StepResult(Outcome.SUCCEEDED, task.result)
        elif task.status is TaskStatus.FAILED:
            steps[task.address] = StepResult(Outcome.FAILED)
        else:
            steps[task.address] = StepResult(Outcome.RUNNING)
    for sub_run in sub_runs:
        assert sub_run.parent_address is not None
        if sub_run.status is RunStatus.SUCCEEDED:
            steps[sub_run.parent_address] = StepResult(
                Outcome.SUCCEEDED, sub_run.output
            )
        elif sub_run.status is RunStatus.ACTIVE:
            steps[sub_run.parent_address] = StepResult(Outcome.RUNNING)
        else:
            steps[sub_run.parent_address] = StepResult(Outcome.FAILED)
    return RunState(run.inputs, steps)
