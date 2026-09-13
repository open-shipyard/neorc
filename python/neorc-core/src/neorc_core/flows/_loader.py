# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Reading flow files: YAML text, or the JSON structure the manager receives.

A flow file and its upload are the same structure, so everything goes through
``parse_flow``. Every problem found is reported at once, each with the path to
where it is.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from neorc_core import _values
from neorc_core._errors import FlowDefinitionError, InvalidValueError
from neorc_core.flows._definition import (
    DEFAULT_QUEUE,
    FanOutStep,
    FlowDefinition,
    InputType,
    LoopStep,
    Reference,
    Step,
    SubFlowStep,
    TaskStep,
    Version,
)
from neorc_core.flows._validation import check_flow, check_flow_set

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_TOP_KEYS = {"name", "version", "inputs", "output", "steps"}
_KIND_KEYS = ("handler", "loop", "fan_out", "flow")
_STEP_KEYS = {
    "handler": {"handler", "queue", "params", "fixed_params"},
    "loop": {"loop", "steps"},
    "fan_out": {"fan_out", "steps"},
    "flow": {"flow", "params", "fixed_params"},
}


class _FlowYamlLoader(yaml.SafeLoader):
    """Safe YAML without timestamp conversion: datetimes are written as tags."""


_FlowYamlLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if not tag.endswith("timestamp")]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def read_flow_yaml(text: str) -> Any:
    """Parse YAML into the JSON structure it stands for, unvalidated."""
    return yaml.load(text, Loader=_FlowYamlLoader)


def load_flow_yaml(text: str) -> FlowDefinition:
    """Parse and validate one flow from YAML text."""
    return parse_flow(read_flow_yaml(text))


def load_flow_file(path: Path) -> FlowDefinition:
    """Parse and validate one flow file."""
    return load_flow_yaml(path.read_text(encoding="utf-8"))


def load_flows(directory: Path) -> list[FlowDefinition]:
    """Load every ``*.yaml`` and ``*.yml`` file in ``directory``, checked as a set."""
    paths = sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])
    definitions = [load_flow_file(path) for path in paths]
    validate_flow_set(definitions)
    return definitions


def validate_flow_set(definitions: Iterable[FlowDefinition]) -> None:
    """Check flows that will be deployed together: names, sub-flows, recursion."""
    problems = check_flow_set(list(definitions))
    if problems:
        raise FlowDefinitionError(problems)


def parse_flow(data: Any) -> FlowDefinition:
    """Build a validated flow definition from its JSON structure."""
    parser = _Parser()
    definition = parser.flow(data)
    if definition is None or parser.problems:
        raise FlowDefinitionError(parser.problems)
    problems = check_flow(definition)
    if problems:
        raise FlowDefinitionError(problems)
    return definition


class _Parser:
    def __init__(self) -> None:
        self.problems: list[str] = []
        self._names: set[str] = set()

    def problem(self, path: str, message: str) -> None:
        self.problems.append(f"{path}: {message}")

    def flow(self, data: Any) -> FlowDefinition | None:
        if not isinstance(data, Mapping):
            self.problem("flow", "must be a mapping")
            return None
        self.keys(data, "flow", allowed=_TOP_KEYS, required={"name", "version"})
        if list(data)[:2] != ["name", "version"]:
            self.problem("flow", "'name' and 'version' must be the first two fields")
        name = self.name(data.get("name"), "name")
        version = self.version(data.get("version"))
        inputs = self.inputs(data.get("inputs", {}))
        output = None
        if "output" in data:
            output = self.reference(data["output"], "output")
        steps = self.steps(data.get("steps"), "steps")
        if name is None or version is None:
            return None
        return FlowDefinition(name, version, inputs, steps, output)

    def keys(
        self,
        data: Mapping[Any, Any],
        path: str,
        *,
        allowed: set[str],
        required: set[str],
    ) -> None:
        for key in data:
            if key not in allowed:
                self.problem(path, f"unknown field {key!r}")
        for key in sorted(required - set(data)):
            self.problem(path, f"missing field {key!r}")

    def name(self, value: Any, path: str) -> str | None:
        if not isinstance(value, str) or not _NAME.fullmatch(value):
            self.problem(path, f"{value!r} is not a name: letters, digits and _")
            return None
        return value

    def version(self, value: Any) -> Version | None:
        if not isinstance(value, str):
            self.problem("version", f"{value!r} must be a string like '1.0.0'")
            return None
        try:
            return Version.parse(value)
        except ValueError as exc:
            self.problem("version", str(exc))
            return None

    def inputs(self, data: Any) -> dict[str, InputType]:
        if not isinstance(data, Mapping):
            self.problem("inputs", "must be a mapping of input names to types")
            return {}
        inputs: dict[str, InputType] = {}
        for key, value in data.items():
            name = self.name(key, f"inputs.{key}")
            try:
                input_type = InputType(value)
            except ValueError:
                known = ", ".join(t.value for t in InputType)
                self.problem(f"inputs.{key}", f"type {value!r} is not one of {known}")
                continue
            if name is not None:
                inputs[name] = input_type
        return inputs

    def steps(self, data: Any, path: str) -> tuple[Step, ...]:
        if not isinstance(data, Mapping) or not data:
            self.problem(path, "must be a non-empty mapping of step names to steps")
            return ()
        steps: list[Step] = []
        for key, value in data.items():
            step = self.step(key, value, f"{path}.{key}")
            if step is not None:
                steps.append(step)
        return tuple(steps)

    def step(self, key: Any, data: Any, path: str) -> Step | None:
        name = self.name(key, path)
        if name is not None:
            if name in self._names:
                self.problem(path, f"step name {name!r} is used more than once")
            self._names.add(name)
        if not isinstance(data, Mapping):
            self.problem(path, "must be a mapping")
            return None
        kinds = [kind for kind in _KIND_KEYS if kind in data]
        if len(kinds) != 1:
            self.problem(path, "must have exactly one of " + ", ".join(_KIND_KEYS))
            return None
        kind = kinds[0]
        self.keys(data, path, allowed=_STEP_KEYS[kind], required=set())
        if name is None:
            return None
        if kind == "handler":
            return TaskStep(
                name,
                handler=self.text(data["handler"], f"{path}.handler"),
                queue=self.text(data.get("queue", DEFAULT_QUEUE), f"{path}.queue"),
                params=self.params(data.get("params", {}), f"{path}.params"),
                fixed_params=self.fixed(data.get("fixed_params", {}), path),
            )
        if kind == "flow":
            return SubFlowStep(
                name,
                flow=self.text(data["flow"], f"{path}.flow"),
                params=self.params(data.get("params", {}), f"{path}.params"),
                fixed_params=self.fixed(data.get("fixed_params", {}), path),
            )
        if kind == "loop":
            return self.loop(name, data, path)
        return self.fan_out(name, data, path)

    def loop(self, name: str, data: Mapping[str, Any], path: str) -> LoopStep | None:
        config = data["loop"]
        steps = self.steps(data.get("steps"), f"{path}.steps")
        if not isinstance(config, Mapping):
            self.problem(f"{path}.loop", "must be a mapping")
            return None
        self.keys(
            config,
            f"{path}.loop",
            allowed={"max_cycles", "exit_condition"},
            required={"max_cycles", "exit_condition"},
        )
        max_cycles = self.positive(config.get("max_cycles"), f"{path}.loop.max_cycles")
        exit_condition = self.text(
            config.get("exit_condition"), f"{path}.loop.exit_condition"
        )
        if max_cycles is None:
            return None
        return LoopStep(name, max_cycles, exit_condition, steps)

    def fan_out(
        self, name: str, data: Mapping[str, Any], path: str
    ) -> FanOutStep | None:
        config = data["fan_out"]
        steps = self.steps(data.get("steps"), f"{path}.steps")
        if not isinstance(config, Mapping):
            self.problem(f"{path}.fan_out", "must be a mapping")
            return None
        self.keys(config, f"{path}.fan_out", allowed={"range", "over"}, required=set())
        if ("range" in config) == ("over" in config):
            self.problem(f"{path}.fan_out", "must have exactly one of 'range', 'over'")
            return None
        if "range" in config:
            width = self.positive(config["range"], f"{path}.fan_out.range")
            return None if width is None else FanOutStep(name, steps, range=width)
        over = self.reference(config["over"], f"{path}.fan_out.over")
        return None if over is None else FanOutStep(name, steps, over=over)

    def params(self, data: Any, path: str) -> dict[str, Reference]:
        if not isinstance(data, Mapping):
            self.problem(path, "must be a mapping of parameter names to references")
            return {}
        params: dict[str, Reference] = {}
        for key, value in data.items():
            name = self.name(key, f"{path}.{key}")
            reference = self.reference(value, f"{path}.{key}")
            if name is not None and reference is not None:
                params[name] = reference
        return params

    def fixed(self, data: Any, step_path: str) -> dict[str, _values.JsonValue]:
        path = f"{step_path}.fixed_params"
        if not isinstance(data, Mapping):
            self.problem(path, "must be a mapping of parameter names to values")
            return {}
        fixed: dict[str, _values.JsonValue] = {}
        for key, value in data.items():
            name = self.name(key, f"{path}.{key}")
            try:
                # Round-trip to check the value is JSON with valid datetime tags.
                encoded = _values.encode(_values.decode(value))
            except InvalidValueError as exc:
                self.problem(f"{path}.{key}", str(exc))
                continue
            if name is not None:
                fixed[name] = encoded
        return fixed

    def reference(self, value: Any, path: str) -> Reference | None:
        if not isinstance(value, str):
            self.problem(path, f"{value!r} is not a reference like 'tasks.name'")
            return None
        try:
            return Reference.parse(value)
        except ValueError as exc:
            self.problem(path, str(exc))
            return None

    def text(self, value: Any, path: str) -> str:
        if not isinstance(value, str) or not value:
            self.problem(path, "must be a non-empty string")
            return ""
        return value

    def positive(self, value: Any, path: str) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            self.problem(path, f"{value!r} must be a whole number of at least 1")
            return None
        return value
