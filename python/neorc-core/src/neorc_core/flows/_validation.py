# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The checks a flow must pass beyond its shape: references, cycles, sub-flows.

Each check returns the problems it found instead of raising, so a flow file with
several mistakes reports all of them.
"""

from __future__ import annotations

from neorc_core.flows._definition import (
    NEORC_METADATA,
    Container,
    FanOutStep,
    FlowDefinition,
    LoopStep,
    Namespace,
    Reference,
    Step,
    SubFlowStep,
    TaskStep,
    step_references,
)

_ROOT = "the flow"


def check_flow(definition: FlowDefinition) -> list[str]:
    """Every problem in one flow that its shape alone does not reveal."""
    problems: list[str] = []
    for step, enclosing in definition.walk():
        if isinstance(step, TaskStep | SubFlowStep):
            for name in sorted(set(step.params) & set(step.fixed_params)):
                problems.append(
                    f"{step.name}: {name!r} is in both params and fixed_params"
                )
        if isinstance(step, LoopStep):
            problems.extend(_check_exit_condition(step))
        for where, reference in step_references(step):
            problems.extend(
                _check_reference(definition, step, enclosing, where, reference)
            )
    if definition.output is not None:
        output = definition.output
        if output.namespace not in (Namespace.TASKS, Namespace.FLOWS):
            problems.append(f"output: {output} must refer to tasks. or flows.")
        else:
            problems.extend(_check_target(definition, "output", output))
    problems.extend(_check_cycles(definition))
    return problems


def check_flow_set(definitions: list[FlowDefinition]) -> list[str]:
    """Problems in flows deployed together: duplicates, sub-flows, recursion."""
    problems: list[str] = []
    by_name: dict[str, FlowDefinition] = {}
    for definition in definitions:
        if definition.name in by_name:
            problems.append(f"flow {definition.name!r} is defined more than once")
        by_name[definition.name] = definition

    calls: dict[str, set[str]] = {name: set() for name in by_name}
    for definition in by_name.values():
        for step, _ in definition.walk():
            if not isinstance(step, SubFlowStep):
                continue
            where = f"{definition.name}.{step.name}"
            called = by_name.get(step.flow)
            if called is None:
                problems.append(f"{where}: calls unknown flow {step.flow!r}")
                continue
            calls[definition.name].add(called.name)
            given = set(step.params) | set(step.fixed_params)
            for name in sorted(set(called.inputs) - given):
                problems.append(f"{where}: missing input {name!r} of {called.name}")
            for name in sorted(given - set(called.inputs)):
                problems.append(f"{where}: {called.name} has no input {name!r}")

    for cycle in _cycles(calls):
        problems.append("flows call each other in a cycle: " + " -> ".join(cycle))
    return problems


def _check_exit_condition(loop: LoopStep) -> list[str]:
    for step in loop.steps:
        if step.name == loop.exit_condition:
            if isinstance(step, TaskStep):
                return []
            return [f"{loop.name}: exit condition {step.name!r} must be a task"]
    return [
        f"{loop.name}: exit condition {loop.exit_condition!r} "
        "is not a task directly in the loop"
    ]


def _check_reference(
    definition: FlowDefinition,
    consumer: Step,
    enclosing: tuple[Container, ...],
    where: str,
    reference: Reference,
) -> list[str]:
    if reference.namespace is Namespace.INPUTS:
        if reference.name not in definition.inputs:
            return [f"{where}: the flow has no input {reference.name!r}"]
        return []
    if reference.namespace is Namespace.NEORC:
        return _check_metadata(consumer, enclosing, where, reference)
    problems = _check_target(definition, where, reference)
    if problems:
        return problems
    if reference.name == consumer.name:
        return [f"{where}: a step cannot refer to itself"]
    if isinstance(consumer, FanOutStep) and consumer.name in (
        c.name for c in definition.enclosing(reference.name)
    ):
        return [f"{where}: {reference} is inside the fan-out it feeds"]
    return []


def _check_target(
    definition: FlowDefinition, where: str, reference: Reference
) -> list[str]:
    try:
        target = definition.step(reference.name)
    except KeyError:
        return [f"{where}: there is no step {reference.name!r}"]
    if reference.namespace is Namespace.TASKS and not isinstance(target, TaskStep):
        return [f"{where}: {reference.name!r} is not a task; tasks. refers to tasks"]
    if reference.namespace is Namespace.FLOWS and not isinstance(target, SubFlowStep):
        return [f"{where}: {reference.name!r} is not a sub-flow; flows. refers to them"]
    return []


def _check_metadata(
    consumer: Step, enclosing: tuple[Container, ...], where: str, reference: Reference
) -> list[str]:
    name = reference.name
    if not isinstance(consumer, TaskStep):
        return [f"{where}: only tasks can request neorc metadata"]
    if name not in NEORC_METADATA:
        known = ", ".join(sorted(NEORC_METADATA))
        return [f"{where}: {reference} is not one of neorc.{{{known}}}"]
    fan_outs = [c for c in enclosing if isinstance(c, FanOutStep)]
    if name == "index" and not fan_outs:
        return [f"{where}: {reference} needs the task to be inside a fan-out"]
    if name == "item" and (not fan_outs or fan_outs[-1].over is None):
        return [f"{where}: {reference} needs the task inside a fan-out over a list"]
    if name == "loop_count" and not any(isinstance(c, LoopStep) for c in enclosing):
        return [f"{where}: {reference} needs the task to be inside a loop"]
    return []


def _check_cycles(definition: FlowDefinition) -> list[str]:
    """Steps that wait on each other, within the scope they share."""
    graphs: dict[str, dict[str, set[str]]] = {}
    for step, enclosing in definition.walk():
        waits_on = definition.waits_on(step.name)
        if waits_on:
            scope = enclosing[-1].name if enclosing else _ROOT
            graphs.setdefault(scope, {})[step.name] = set(waits_on)

    problems = []
    for scope, edges in graphs.items():
        for cycle in _cycles(edges):
            where = scope if scope == _ROOT else repr(scope)
            problems.append(
                f"steps wait on each other in {where}: " + " -> ".join(cycle)
            )
    return problems


def _cycles(edges: dict[str, set[str]]) -> list[list[str]]:
    """One cycle per strongly connected loop found by depth-first search."""
    found: list[list[str]] = []
    done: set[str] = set()

    def visit(node: str, path: list[str]) -> None:
        if node in path:
            found.append([*path[path.index(node) :], node])
            return
        if node in done:
            return
        path.append(node)
        for following in sorted(edges.get(node, ())):
            visit(following, path)
        path.pop()
        done.add(node)

    for node in sorted(edges):
        visit(node, [])
    return found
