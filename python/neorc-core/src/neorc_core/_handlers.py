# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Finding the Python function a task's handler names, and checking it fits.

A handler is ``module:function``, imported from a worker's code location first
and then from the usual import path. See docs/specs/workers-and-manager.md,
"Handlers".
"""

from __future__ import annotations

import importlib
import inspect
import sys
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any


def resolve_handler(
    target: object, code_location: Path | None = None
) -> Callable[..., Any]:
    """The callable ``module:function`` names; ``ValueError`` saying why if none.

    ``code_location``, when given, goes to the front of the import path.
    """
    if not isinstance(target, str) or ":" not in target:
        raise ValueError(f'wants "module:function", not {target!r}')
    if code_location is not None:
        location = str(code_location.resolve())
        if not sys.path or sys.path[0] != location:
            if location in sys.path:
                sys.path.remove(location)
            sys.path.insert(0, location)
    module_name, _, attribute = target.partition(":")
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # a syntax error or a failing module body, too
        raise ValueError(f"cannot import {module_name!r}: {exc}") from exc
    function: object = getattr(module, attribute, None)
    if not callable(function):
        raise ValueError(f"{module_name!r} has no function {attribute!r}")
    return function


def signature_problems(
    function: Callable[..., Any], names: Collection[str]
) -> list[str]:
    """How a handler's parameters disagree with the inputs a task declares.

    Every declared input must be a parameter the handler accepts, by name or
    through ``**kwargs``, and every parameter without a default must be declared.
    """
    try:
        parameters = inspect.signature(function).parameters.values()
    except (TypeError, ValueError):
        return []  # no signature to read: trust the call
    by_name = {
        p.name: p
        for p in parameters
        if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
    }
    takes_any = any(p.kind is p.VAR_KEYWORD for p in parameters)
    problems = [
        f"takes no parameter {name!r}"
        for name in sorted(names)
        if name not in by_name and not takes_any
    ]
    problems.extend(
        f"parameter {name!r} is not an input of the task"
        for name, parameter in by_name.items()
        if name not in names and parameter.default is parameter.empty
    )
    problems.extend(
        f"parameter {p.name!r} is positional-only and cannot be passed by name"
        for p in parameters
        if p.kind is p.POSITIONAL_ONLY and p.default is p.empty
    )
    return problems
