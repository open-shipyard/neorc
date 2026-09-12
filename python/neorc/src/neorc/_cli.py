# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The ``neorc`` command.

    neorc manager start
    neorc worker start

Adapters are imported inside the handlers, so a host that installed only the
extras it needs can still run the CLI.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

MANAGER_ADDRESS_ENV = "NEORC_MANAGER_ADDRESS"


def build_parser() -> argparse.ArgumentParser:
    """Build the ``neorc`` argument parser and its subcommands."""
    raise NotImplementedError


def manager_start(args: argparse.Namespace) -> int:
    """Run the manager service. Needs the ``manager`` extra."""
    raise NotImplementedError


def worker_start(args: argparse.Namespace) -> int:
    """Run a worker against the manager named by ``NEORC_MANAGER_ADDRESS``."""
    raise NotImplementedError


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``neorc`` console script; returns the exit status."""
    raise NotImplementedError
