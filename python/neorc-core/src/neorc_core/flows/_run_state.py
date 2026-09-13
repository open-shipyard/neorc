# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""A snapshot of one run: what its steps have produced so far.

The planner and reference resolution read a run through this, never through a
store, so both stay pure. The store builds it from its tasks and sub-flow runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cached_property

from neorc_core._values import JsonValue

Scope = tuple[tuple[str, int], ...]
"""Where a step instance is: each enclosing loop or fan-out, outermost first,
with its iteration or index. Both count from 1."""


@dataclass(frozen=True, order=True, slots=True)
class Address:
    """One instance of a step in a run: ``rounds[2].picking[3].pick_word``.

    A step outside every loop and fan-out has one instance, with an empty scope.
    The same step, iteration and index always give the same address.
    """

    step: str
    scope: Scope = ()

    def __str__(self) -> str:
        return ".".join([*(f"{name}[{n}]" for name, n in self.scope), self.step])


class Outcome(StrEnum):
    """How far a task or sub-flow run has got."""

    RUNNING = "running"
    """Published or started, and not finished yet."""
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class StepResult:
    """What one task or sub-flow run instance came to.

    ``value`` is a task's result or a sub-flow's output, in its JSON form, and
    is only meaningful once the instance succeeded.
    """

    outcome: Outcome
    value: JsonValue = None


@dataclass(frozen=True)
class RunState:
    """A run's inputs and every task and sub-flow run instance it has started.

    Values are in their JSON form, datetimes still tagged: resolution hands them
    on unchanged, and only the worker decodes them for a handler.
    """

    inputs: Mapping[str, JsonValue] = field(default_factory=dict)
    steps: Mapping[Address, StepResult] = field(default_factory=dict)

    def get(self, address: Address) -> StepResult | None:
        """The instance at ``address``, or ``None`` if it has not been started."""
        return self.steps.get(address)

    def last(self, scope: Scope, container: str) -> int:
        """The highest iteration or index of ``container`` started within ``scope``.

        ``0`` when no step inside it has been started there.
        """
        return self._last.get((scope, container), 0)

    @cached_property
    def _last(self) -> dict[tuple[Scope, str], int]:
        last: dict[tuple[Scope, str], int] = {}
        for address in self.steps:
            for depth, (name, number) in enumerate(address.scope):
                key = (address.scope[:depth], name)
                last[key] = max(last.get(key, 0), number)
        return last
