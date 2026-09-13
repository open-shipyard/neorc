# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Two trivial handlers, for trying neorc out locally. See README.md."""

from __future__ import annotations

from neorc import Task, Worker


async def a(task: Task) -> None:
    print("a")


async def b(task: Task) -> None:
    print("b")


def setup(worker: Worker) -> None:
    worker.register("a", a)
    worker.register("b", b)
