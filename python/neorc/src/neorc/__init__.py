# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""A next generation orchestration system."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("neorc")
except PackageNotFoundError:  # a source checkout on sys.path, not installed
    __version__ = "0+unknown"

__all__ = ["__version__"]
