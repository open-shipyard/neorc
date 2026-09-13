# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The tables the store needs, and how to create them.

The statements are idempotent, so running them against an up-to-date database
does nothing. This is a create-if-absent step, not a migration tool: changing
the shape of an existing table is out of scope until there is a released
version to migrate from.
"""

from __future__ import annotations

import psycopg

TASKS_TABLE = "neorc_tasks"

CREATE_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TASKS_TABLE} (
    id               uuid PRIMARY KEY,
    name             text NOT NULL,
    payload          jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    status           text NOT NULL
                     CHECK (status IN ('pending', 'claimed', 'running',
                                       'succeeded', 'failed')),
    created_at       timestamptz NOT NULL DEFAULT now(),
    run_after        timestamptz NOT NULL DEFAULT now(),
    priority         integer NOT NULL DEFAULT 0,
    attempts         integer NOT NULL DEFAULT 0,
    lease_expires_at timestamptz,
    error            text
);

-- The claim query's index: due tasks that are not finished, best first. Partial,
-- so finished tasks stop costing anything to skip over as the table grows.
CREATE INDEX IF NOT EXISTS {TASKS_TABLE}_claimable_idx
    ON {TASKS_TABLE} (priority DESC, run_after)
    WHERE status IN ('pending', 'claimed', 'running');
"""

DROP_SCHEMA = f"DROP TABLE IF EXISTS {TASKS_TABLE}"


async def create_schema(dsn: str) -> None:
    """Create the neorc tables and indexes if they do not exist."""
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute(CREATE_SCHEMA)


async def drop_schema(dsn: str) -> None:
    """Remove the neorc tables. For tests and teardown."""
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute(DROP_SCHEMA)
