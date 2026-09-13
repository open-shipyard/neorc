# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""What the manager does for flows, against the store port.

The manager records and queues; it does not decide what happens next, which is
the scheduler's job. It validates what it is asked to record and rejects what is
invalid. ``Manager`` keeps serving the task API next to it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from neorc_core import _values
from neorc_core._errors import (
    FlowDefinitionError,
    InvalidValueError,
    RunStateError,
)
from neorc_core._runs import Run, RunId, RunStatus, StoredFlow
from neorc_core._values import JsonValue
from neorc_core.flows import (
    FlowDefinition,
    InputType,
    Reference,
    parse_flow,
    resolve,
)
from neorc_core.ports._store import Store

CANCELLED_BY_HAND = "cancelled by hand"


class FlowManager:
    """Serves flow uploads, runs and, later, the scheduler and workers."""

    def __init__(self, store: Store) -> None:
        self._store = store

    async def upload_flows(self, contents: Sequence[JsonValue]) -> list[bool]:
        """Validate flows deployed together, then store each version.

        ``contents`` are flow files as their JSON structure. The flows that will
        be latest are checked as a set, so a sub-flow may be uploaded with its
        caller or before it. Returns, per flow, whether a new version was
        stored; ``False`` for an identical upload.

        Raises ``FlowDefinitionError`` for an invalid flow or set, and
        ``FlowVersionError`` for a version rule broken; either way nothing is
        stored.
        """
        definitions: list[FlowDefinition] = []
        problems: list[str] = []
        for index, content in enumerate(contents):
            try:
                definitions.append(parse_flow(content))
            except FlowDefinitionError as exc:
                problems.extend(f"flow {index + 1}: {p}" for p in exc.problems)
        if problems:
            raise FlowDefinitionError(problems)

        return await self._store.store_flows(
            [
                StoredFlow(definition, content)
                for definition, content in zip(definitions, contents, strict=True)
            ]
        )

    async def start_run(self, flow: str, inputs: Mapping[str, JsonValue]) -> Run:
        """Start a run of ``flow``'s latest version.

        ``inputs`` are in their JSON form, and must be exactly the flow's
        declared inputs, each of its declared type: ``InvalidValueError``
        otherwise. ``FlowNotFoundError`` if there is no such flow.
        """
        stored = await self._store.get_flow(flow)
        check_inputs(stored.definition, inputs)
        return await self._store.start_run(flow, stored.version, inputs)

    async def get_run(self, run_id: RunId) -> Run:
        """A run, for a status query."""
        return await self._store.get_run(run_id)

    async def cancel_run(self, run_id: RunId, reason: str = CANCELLED_BY_HAND) -> None:
        """Cancel a run by hand, and with it every active run in its tree.

        Raises ``RunStateError`` if the run has already finished.
        """
        run = await self._store.get_run(run_id)
        if run.status is not RunStatus.ACTIVE:
            raise RunStateError(f"run {run_id} is already {run.status.value}")
        await self._store.cancel_run_tree(run_id, reason)

    async def fail_run(self, run_id: RunId, reason: str) -> None:
        """Fail every active run in a run's tree; nothing changes if none is."""
        await self._store.fail_run_tree(run_id, reason)

    async def succeed_run(self, run_id: RunId, output: Reference | None) -> Run:
        """Mark an active run succeeded, with the value of ``output`` if any.

        Raises ``RunStateError`` if the run is not active, or its output is not
        available yet.
        """
        value: JsonValue = None
        if output is not None:
            run = await self._store.get_run(run_id)
            definition = (await self._store.get_flow(run.flow, run.version)).definition
            state = await self._store.run_state(run_id)
            resolved = resolve(definition, state, None, output)
            if resolved is None:
                raise RunStateError(f"run {run_id}: {output} is not available yet")
            value = resolved.value
        return await self._store.succeed_run(run_id, value)


def check_inputs(definition: FlowDefinition, inputs: Mapping[str, JsonValue]) -> None:
    """Raise ``InvalidValueError`` unless ``inputs`` match the flow's declared inputs.

    Inputs are in their JSON form, so a datetime is a ``$datetime`` tag.
    """
    problems = [
        f"missing input {name!r}" for name in definition.inputs if name not in inputs
    ]
    for name, value in inputs.items():
        declared = definition.inputs.get(name)
        if declared is None:
            problems.append(f"{definition.name} has no input {name!r}")
        elif not _is_of_type(value, declared):
            problems.append(f"input {name!r} is not a {declared.value}: {value!r}")
    if problems:
        raise InvalidValueError(f"{definition.name}: " + "; ".join(problems))


def _is_of_type(value: JsonValue, declared: InputType) -> bool:
    if declared is InputType.STRING:
        return isinstance(value, str)
    if declared is InputType.BOOLEAN:
        return isinstance(value, bool)
    if declared is InputType.NUMBER:
        return isinstance(value, int | float) and not isinstance(value, bool)
    try:
        return isinstance(_values.decode(value), datetime)
    except InvalidValueError:
        return False
