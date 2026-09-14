# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The flow tables, against a real server.

What the store will build on: the tables and indexes are there after
``create_schema``, twice over; the ``CHECK`` constraints say what the enums
say; and a row written from a dataclass reads back as the same dataclass, with
numbers as JSON wrote them.
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum

import psycopg
import pytest
from psycopg.rows import DictRow, dict_row

from neorc.postgres import create_schema, drop_schema
from neorc.postgres._rows import (
    EVENT_COLUMNS,
    FLOW_COLUMNS,
    RUN_COLUMNS,
    TASK_COLUMNS,
    event_from_row,
    event_to_row,
    flow_from_row,
    flow_to_row,
    run_from_row,
    run_to_row,
    task_from_row,
    task_to_row,
)
from neorc.postgres._schema import (
    EVENTS_TABLE,
    FLOW_TASKS_TABLE,
    FLOW_VERSIONS_TABLE,
    INDEXES,
    RUNS_TABLE,
    TABLES,
    TASKS_TABLE,
)
from neorc_core import (
    Event,
    EventKind,
    FlowTask,
    Run,
    RunStatus,
    StoredFlow,
    TaskStatus,
)
from neorc_core._runs import canonical_content
from neorc_core._values import JsonValue
from neorc_core.flows import Address, Reference, Version, parse_flow

pytestmark = pytest.mark.postgres


@pytest.fixture
async def conn(pg_schema: str) -> AsyncIterator[psycopg.AsyncConnection[DictRow]]:
    async with await psycopg.AsyncConnection.connect(
        pg_schema, autocommit=True, row_factory=dict_row
    ) as connection:
        yield connection


async def _names(conn: psycopg.AsyncConnection[DictRow], query: str) -> set[str]:
    cursor = await conn.execute(query)
    return {row["name"] for row in await cursor.fetchall()}


_TABLES = "SELECT tablename AS name FROM pg_tables WHERE tablename LIKE 'neorc_%'"
_INDEXES = "SELECT indexname AS name FROM pg_indexes WHERE tablename LIKE 'neorc_%'"


async def test_create_schema_is_idempotent_and_creates_everything(
    database_url: str,
) -> None:
    await drop_schema(database_url)
    await create_schema(database_url)
    await create_schema(database_url)

    async with await psycopg.AsyncConnection.connect(
        database_url, row_factory=dict_row
    ) as conn:
        assert await _names(conn, _TABLES) == set(TABLES)
        assert set(INDEXES) <= await _names(conn, _INDEXES)

    await drop_schema(database_url)
    async with await psycopg.AsyncConnection.connect(
        database_url, row_factory=dict_row
    ) as conn:
        assert await _names(conn, _TABLES) == set()


_CONSTRAINT = """
SELECT pg_get_constraintdef(c.oid) AS definition
  FROM pg_constraint AS c
  JOIN pg_class AS t ON t.oid = c.conrelid
 WHERE c.contype = 'c' AND t.relname = %(table)s
   AND pg_get_constraintdef(c.oid) LIKE %(column)s
"""


@pytest.mark.parametrize(
    ("table", "column", "enum"),
    [
        (TASKS_TABLE, "status", TaskStatus),
        (FLOW_TASKS_TABLE, "status", TaskStatus),
        (RUNS_TABLE, "status", RunStatus),
        (EVENTS_TABLE, "kind", EventKind),
    ],
    ids=["tasks", "flow-tasks", "runs", "events"],
)
async def test_check_constraints_are_in_step_with_the_enums(
    conn: psycopg.AsyncConnection[DictRow],
    table: str,
    column: str,
    enum: type[StrEnum],
) -> None:
    cursor = await conn.execute(
        _CONSTRAINT, {"table": table, "column": f"CHECK (({column} = ANY%"}
    )
    (row,) = await cursor.fetchall()

    literals = re.findall(r"'([^']*)'", row["definition"])
    assert sorted(literals) == sorted(member.value for member in enum)


def _flow(content: JsonValue) -> StoredFlow:
    return StoredFlow(parse_flow(content), content)


async def test_a_flow_version_is_stored_as_canonical_json(
    conn: psycopg.AsyncConnection[DictRow],
) -> None:
    content: JsonValue = {
        "version": "1.2.3",
        "steps": {"work": {"handler": "tasks:work", "fixed_params": {"n": 1.0}}},
        "name": "a",
    }
    flow = _flow(content)

    await conn.execute(
        f"INSERT INTO {FLOW_VERSIONS_TABLE} ({FLOW_COLUMNS}) VALUES "
        "(%(name)s, %(major)s, %(minor)s, %(patch)s, %(content)s)",
        flow_to_row(flow),
    )
    cursor = await conn.execute(f"SELECT {FLOW_COLUMNS} FROM {FLOW_VERSIONS_TABLE}")
    (row,) = await cursor.fetchall()

    assert row["content"] == canonical_content(content)
    assert row["content"].startswith('{"name":"a","steps":')
    assert (row["major"], row["minor"], row["patch"]) == (1, 2, 3)
    read = flow_from_row(row)
    assert read == flow
    assert read.version == Version(1, 2, 3)


WHEN = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)
NUMBERS: list[JsonValue] = [1e16, -0.0, 10**20, 1.0, 1]


def _same_numbers(read: JsonValue) -> None:
    """Each number came back as JSON wrote it: type, sign and all."""
    assert isinstance(read, list)
    big, negative_zero, huge, whole, integer = read
    assert isinstance(big, float) and big == 1e16
    assert isinstance(negative_zero, float) and math.copysign(1, negative_zero) < 0
    assert isinstance(huge, int) and huge == 10**20
    assert isinstance(whole, float)
    assert isinstance(integer, int) and not isinstance(integer, bool)


async def _insert_run(conn: psycopg.AsyncConnection[DictRow], run: Run) -> Run:
    await conn.execute(
        f"INSERT INTO {RUNS_TABLE} ({RUN_COLUMNS}) VALUES "
        "(%(id)s, %(flow)s, %(version)s, %(inputs)s, %(status)s, %(root_id)s, "
        "%(parent_id)s, %(parent_address)s, %(output)s, %(reason)s)",
        run_to_row(run),
    )
    cursor = await conn.execute(
        f"SELECT {RUN_COLUMNS} FROM {RUNS_TABLE} WHERE id = %s", (run.id,)
    )
    (row,) = await cursor.fetchall()
    return run_from_row(row)


async def test_runs_round_trip(conn: psycopg.AsyncConnection[DictRow]) -> None:
    root_id = uuid.uuid4()
    root = Run(
        id=root_id,
        flow="a",
        version=Version(1, 0, 0),
        inputs={"numbers": NUMBERS, "when": {"$datetime": WHEN.isoformat()}},
        status=RunStatus.ACTIVE,
        root_id=root_id,
    )
    sub_run = Run(
        id=uuid.uuid4(),
        flow="b",
        version=Version(2, 10, 0),
        inputs={},
        status=RunStatus.FAILED,
        root_id=root_id,
        parent_id=root_id,
        parent_address=Address("call", (("rounds", 2), ("picking", 3))),
        output={"words": ["red"]},
        reason="a task failed",
    )

    read_root = await _insert_run(conn, root)
    read_sub_run = await _insert_run(conn, sub_run)

    assert read_root == root
    assert read_sub_run == sub_run
    assert isinstance(read_root.inputs, dict)
    _same_numbers(read_root.inputs["numbers"])


async def test_tasks_round_trip_and_are_ordered_by_publication(
    conn: psycopg.AsyncConnection[DictRow],
) -> None:
    run_id = uuid.uuid4()
    pending = FlowTask(
        id=uuid.uuid4(),
        run_id=run_id,
        address=Address("work", (("rounds", 1),)),
        queue="default",
        handler="tasks:work",
        params={
            "x": Reference.parse("inputs.x"),
            "n": Reference.parse("neorc.attempts"),
        },
        fixed_params={"k": {"$datetime": WHEN.isoformat()}, "numbers": NUMBERS},
    )
    finished = FlowTask(
        id=uuid.uuid4(),
        run_id=run_id,
        address=Address("other"),
        queue="voice",
        handler="tasks:say",
        status=TaskStatus.SUCCEEDED,
        attempts=2,
        lease_expires_at=WHEN,
        result=NUMBERS,
        error=None,
    )
    for task in (pending, finished):
        await conn.execute(
            f"INSERT INTO {FLOW_TASKS_TABLE} ({TASK_COLUMNS}) VALUES "
            "(%(id)s, %(run_id)s, %(address)s, %(queue)s, %(handler)s, %(params)s, "
            "%(fixed_params)s, %(status)s, %(attempts)s, %(lease_expires_at)s, "
            "%(result)s, %(error)s)",
            task_to_row(task),
        )

    cursor = await conn.execute(
        f"SELECT {TASK_COLUMNS} FROM {FLOW_TASKS_TABLE} ORDER BY position"
    )
    rows = await cursor.fetchall()

    read_pending, read_finished = (task_from_row(row) for row in rows)
    assert read_pending == pending
    assert read_finished == finished
    assert isinstance(read_pending.fixed_params["numbers"], list)
    _same_numbers(read_pending.fixed_params["numbers"])
    _same_numbers(read_finished.result)


async def test_events_round_trip(conn: psycopg.AsyncConnection[DictRow]) -> None:
    event = Event(7, uuid.uuid4(), EventKind.TASK_FINISHED)

    await conn.execute(
        f"INSERT INTO {EVENTS_TABLE} ({EVENT_COLUMNS}) VALUES "
        "(%(sequence)s, %(run_id)s, %(kind)s)",
        event_to_row(event),
    )
    cursor = await conn.execute(f"SELECT {EVENT_COLUMNS} FROM {EVENTS_TABLE}")
    (row,) = await cursor.fetchall()

    assert event_from_row(row) == event
    with pytest.raises(psycopg.errors.UniqueViolation):
        await conn.execute(
            f"INSERT INTO {EVENTS_TABLE} ({EVENT_COLUMNS}) VALUES "
            "(%(sequence)s, %(run_id)s, %(kind)s)",
            event_to_row(event),
        )
