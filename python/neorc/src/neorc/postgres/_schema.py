# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The tables the store needs, and how to create them.

The statements are idempotent, so running them against an up-to-date database
does nothing. This is a create-if-absent step, not a migration tool: changing
the shape of an existing table is out of scope until there is a released
version to migrate from.

The tables follow docs/working-notes/postgres-http-implementation-plan.md:

- Values (run inputs and outputs, task params, fixed params and results) are
  ``text`` holding their compact JSON, not ``jsonb``: ``jsonb`` keeps numbers as
  ``numeric`` and would hand ``1e16`` back as an integer and ``-0.0`` as
  ``0.0``, where the memory store keeps them as written.
- A flow version's content is the canonical JSON text of the upload's
  structure, never the text as it was sent.
- No foreign keys between the flow tables: an insert would take a ``KEY
  SHARE`` lock on the run it references, outside the store's lock order. The
  store's operations keep the references whole.
- The ``CHECK`` constraints mirror ``TaskStatus``, ``RunStatus`` and
  ``EventKind``; a test keeps them in step with the enums.
"""

from __future__ import annotations

import psycopg

FLOW_VERSIONS_TABLE = "neorc_flow_versions"
RUNS_TABLE = "neorc_runs"
FLOW_TASKS_TABLE = "neorc_flow_tasks"
EVENTS_TABLE = "neorc_events"

TABLES = (FLOW_VERSIONS_TABLE, RUNS_TABLE, FLOW_TASKS_TABLE, EVENTS_TABLE)
"""Every table ``create_schema`` creates."""

RETIRED_TABLES = ("neorc_tasks",)
"""Tables of earlier versions, no longer created; ``drop_schema`` removes them too."""

INDEXES = (
    f"{RUNS_TABLE}_root_idx",
    f"{RUNS_TABLE}_parent_idx",
    f"{RUNS_TABLE}_active_by_flow_idx",
    f"{FLOW_TASKS_TABLE}_run_idx",
    f"{FLOW_TASKS_TABLE}_claim_idx",
)
"""Every index ``create_schema`` creates, besides primary keys."""

CREATE_SCHEMA = f"""
-- One row per flow version. The version is three integers so the latest is
-- ORDER BY major, minor, patch, not a text sort.
CREATE TABLE IF NOT EXISTS {FLOW_VERSIONS_TABLE} (
    name             text NOT NULL,
    major            integer NOT NULL,
    minor            integer NOT NULL,
    patch            integer NOT NULL,
    content          text NOT NULL,
    PRIMARY KEY (name, major, minor, patch)
);

CREATE TABLE IF NOT EXISTS {RUNS_TABLE} (
    id               uuid PRIMARY KEY,
    flow             text NOT NULL,
    version          text NOT NULL,
    inputs           text NOT NULL,
    status           text NOT NULL
                     CHECK (status IN ('active', 'succeeded', 'failed',
                                       'cancelled')),
    root_id          uuid NOT NULL,
    parent_id        uuid,
    parent_address   text,
    output           text NOT NULL DEFAULT 'null',
    reason           text
);

-- A tree is finished by its root: every run of a root, and a run's sub-runs
-- for its state.
CREATE INDEX IF NOT EXISTS {RUNS_TABLE}_root_idx ON {RUNS_TABLE} (root_id);
CREATE INDEX IF NOT EXISTS {RUNS_TABLE}_parent_idx ON {RUNS_TABLE} (parent_id);

-- An upload cancels the active trees running its flow.
CREATE INDEX IF NOT EXISTS {RUNS_TABLE}_active_by_flow_idx
    ON {RUNS_TABLE} (flow)
    WHERE status = 'active';

-- position orders tasks by publication: their ids come from their address and
-- carry no order. An insert that hits ON CONFLICT still consumes a value, so
-- positions have gaps; only their order means anything.
CREATE TABLE IF NOT EXISTS {FLOW_TASKS_TABLE} (
    id               uuid PRIMARY KEY,
    run_id           uuid NOT NULL,
    address          text NOT NULL,
    queue            text NOT NULL,
    handler          text NOT NULL,
    params           text NOT NULL,
    fixed_params     text NOT NULL,
    status           text NOT NULL
                     CHECK (status IN ('pending', 'claimed', 'running',
                                       'succeeded', 'failed')),
    attempts         integer NOT NULL DEFAULT 0,
    lease_expires_at timestamptz,
    result           text NOT NULL DEFAULT 'null',
    error            text,
    position         bigint GENERATED ALWAYS AS IDENTITY
);

CREATE INDEX IF NOT EXISTS {FLOW_TASKS_TABLE}_run_idx ON {FLOW_TASKS_TABLE} (run_id);

-- The claim query's index: the oldest unfinished task of a queue. Partial, so
-- finished tasks cost nothing to skip over.
CREATE INDEX IF NOT EXISTS {FLOW_TASKS_TABLE}_claim_idx
    ON {FLOW_TASKS_TABLE} (queue, position)
    WHERE status IN ('pending', 'claimed', 'running');

-- Sequences are allocated by the store, under its event lock, as max + 1: an
-- identity column would let a later transaction commit a lower sequence first.
CREATE TABLE IF NOT EXISTS {EVENTS_TABLE} (
    sequence         bigint PRIMARY KEY,
    run_id           uuid NOT NULL,
    kind             text NOT NULL
                     CHECK (kind IN ('run_started', 'task_finished',
                                     'run_finished'))
);
"""

DROP_SCHEMA = f"DROP TABLE IF EXISTS {', '.join(TABLES + RETIRED_TABLES)}"


async def create_schema(dsn: str) -> None:
    """Create the neorc tables and indexes if they do not exist."""
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute(CREATE_SCHEMA)


async def drop_schema(dsn: str) -> None:
    """Remove the neorc tables. For tests and teardown."""
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute(DROP_SCHEMA)
