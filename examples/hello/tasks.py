# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Two trivial handlers, for trying neorc out locally. See README.md.

Plain functions: tasks.toml maps task names to them, so nothing here imports
neorc.
"""


def a(task):
    print("a")
    print(task)


def b(task):
    print("b")
