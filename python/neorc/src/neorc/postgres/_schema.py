# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Creating the tables the store needs.

Whether migrations ship with the package or are the user's to run is still
open; see docs/specs/postgres-implementation.md.
"""

from __future__ import annotations


async def create_schema(dsn: str) -> None:
    """Create the neorc tables and indexes if they do not exist."""
    raise NotImplementedError


async def drop_schema(dsn: str) -> None:
    """Remove the neorc tables. For tests and teardown."""
    raise NotImplementedError
