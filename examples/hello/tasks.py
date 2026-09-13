# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Two trivial handlers, for trying neorc out locally. See README.md.

Plain functions: each flow file in flows/ names its handler by import path, so
nothing here imports neorc. They declare no inputs, so they take no parameters.
"""


def a():
    print("a")


def b():
    print("b")
