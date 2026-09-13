# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""A flow definition, as neorc holds it once a flow file has been read and checked.

Build one with ``neorc_core.flows.parse_flow`` or the loaders next to it, which
validate as they go; the classes here trust what they are given.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cached_property

from neorc_core._values import JsonValue

_VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")

DEFAULT_QUEUE = "default"


class InputType(StrEnum):
    """The type a flow declares for one of its inputs."""

    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATETIME = "datetime"


class Namespace(StrEnum):
    """What a reference points at: the part before the dot."""

    INPUTS = "inputs"
    TASKS = "tasks"
    FLOWS = "flows"
    NEORC = "neorc"


NEORC_METADATA = frozenset(
    {"task_id", "attempts", "flow_run_id", "loop_count", "index", "item"}
)
"""What a task can request under ``neorc.``."""


@dataclass(frozen=True, slots=True)
class Reference:
    """A value a step takes from elsewhere: ``tasks.pick_word``, ``inputs.sentence``."""

    namespace: Namespace
    name: str

    @classmethod
    def parse(cls, text: str) -> Reference:
        """Read ``namespace.name``; raise ``ValueError`` if it is not one."""
        namespace, dot, name = text.partition(".")
        if not dot or not name or "." in name:
            raise ValueError(f"{text!r} is not a reference like 'tasks.name'")
        try:
            return cls(Namespace(namespace), name)
        except ValueError:
            known = ", ".join(n.value for n in Namespace)
            raise ValueError(
                f"{text!r} starts with {namespace!r}, not one of {known}"
            ) from None

    def __str__(self) -> str:
        return f"{self.namespace.value}.{self.name}"


@dataclass(frozen=True, order=True, slots=True)
class Version:
    """A flow version: ``MAJOR.MINOR.PATCH``, compared numerically."""

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, text: str) -> Version:
        """Read ``MAJOR.MINOR.PATCH``; raise ``ValueError`` if it is not one."""
        match = _VERSION.fullmatch(text)
        if match is None:
            raise ValueError(f"{text!r} is not a version like '1.0.0'")
        major, minor, patch = (int(part) for part in match.groups())
        return cls(major, minor, patch)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True, slots=True)
class TaskStep:
    """A task: a handler run by a worker on ``queue``."""

    name: str
    handler: str
    queue: str = DEFAULT_QUEUE
    params: Mapping[str, Reference] = field(default_factory=dict)
    fixed_params: Mapping[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LoopStep:
    """Steps repeated until ``exit_condition``, a task in the body, returns true."""

    name: str
    max_cycles: int
    exit_condition: str
    steps: tuple[Step, ...]


@dataclass(frozen=True, slots=True)
class FanOutStep:
    """Steps run once per index: ``range`` copies, or one per element of ``over``."""

    name: str
    steps: tuple[Step, ...]
    range: int | None = None
    over: Reference | None = None


@dataclass(frozen=True, slots=True)
class SubFlowStep:
    """A run of another flow, always its latest version."""

    name: str
    flow: str
    params: Mapping[str, Reference] = field(default_factory=dict)
    fixed_params: Mapping[str, JsonValue] = field(default_factory=dict)


Step = TaskStep | LoopStep | FanOutStep | SubFlowStep
Container = LoopStep | FanOutStep


@dataclass(frozen=True)
class FlowDefinition:
    """A flow file, validated."""

    name: str
    version: Version
    inputs: Mapping[str, InputType]
    steps: tuple[Step, ...]
    output: Reference | None = None

    def walk(self) -> Iterator[tuple[Step, tuple[Container, ...]]]:
        """Every step at any depth, with its enclosing loops and fan-outs.

        Enclosing containers come outermost first. Steps come in file order,
        a container before the steps inside it.
        """
        yield from _walk(self.steps, ())

    def step(self, name: str) -> Step:
        """The step called ``name``, at any depth. ``KeyError`` if there is none."""
        return self._index[name][0]

    def enclosing(self, name: str) -> tuple[Container, ...]:
        """The loops and fan-outs around step ``name``, outermost first."""
        return self._index[name][1]

    def tasks(self) -> Iterator[TaskStep]:
        """Every task step, at any depth."""
        for step, _ in self.walk():
            if isinstance(step, TaskStep):
                yield step

    @cached_property
    def _index(self) -> dict[str, tuple[Step, tuple[Container, ...]]]:
        return {step.name: (step, enclosing) for step, enclosing in self.walk()}


def _walk(
    steps: tuple[Step, ...], enclosing: tuple[Container, ...]
) -> Iterator[tuple[Step, tuple[Container, ...]]]:
    for step in steps:
        yield step, enclosing
        if isinstance(step, LoopStep | FanOutStep):
            yield from _walk(step.steps, (*enclosing, step))
