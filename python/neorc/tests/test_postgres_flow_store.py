# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The Postgres store for flows, against a real server, held to the store contract.

Tasks, leases and run state arrive in the next step: their contract tests are
expected to fail until then, strictly, so the step that adds them is the step
that turns them green.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable, Coroutine
from typing import Any

import pytest
from psycopg.conninfo import make_conninfo

from neorc.postgres import PostgresStore
from neorc.postgres._schema import RUNS_TABLE
from neorc_core import RunStatus, Store, StoredFlow
from neorc_core._values import JsonValue
from neorc_core.flows import Address, Version, parse_flow
from neorc_core.testing.contracts import StoreContract

pytestmark = pytest.mark.postgres

_NOT_YET = [
    "test_a_published_task_is_pending_under_its_address",
    "test_publishing_an_address_again_changes_nothing",
    "test_no_task_is_published_into_an_inactive_run",
    "test_claims_are_per_queue_oldest_first",
    "test_concurrent_claims_never_share_a_task",
    "test_a_lapsed_lease_hands_the_task_on",
    "test_a_heartbeat_keeps_the_task",
    "test_a_task_finishes_with_its_result_and_an_event",
    "test_a_task_fails_with_its_reason",
    "test_an_error_is_stored_with_nul_replaced",
    "test_task_transitions_are_enforced",
    "test_a_task_of_an_inactive_run_is_refused_its_start",
    "test_a_task_in_flight_may_finish_after_its_run_was_cancelled",
    "test_an_unknown_task_is_reported_as_missing",
    "test_a_runs_state_holds_its_tasks_and_sub_flow_runs",
    "test_numbers_read_back_as_they_were_written",
    "test_an_unknown_run_is_reported_as_missing",
]
"""The contract's tests that need tasks or run state."""

_Test = Callable[..., Coroutine[Any, Any, None]]


def _until_step_3(test: _Test) -> _Test:
    """The test, expected to fail on ``NotImplementedError`` for now.

    A wrapper, so the mark lands on this class's copy and not on the contract
    the memory store runs too.
    """

    @functools.wraps(test)
    async def not_yet(self: StoreContract, *args: Any, **kwargs: Any) -> None:
        await test(self, *args, **kwargs)

    mark = pytest.mark.xfail(
        strict=True, raises=NotImplementedError, reason="tasks arrive in step 3"
    )
    marked: _Test = mark(not_yet)
    return marked


class TestPostgresStore(StoreContract):
    @pytest.fixture
    def store(self, pg_flow_store: PostgresStore) -> Store:
        return pg_flow_store


async def test_transactions_run_in_read_committed_whatever_the_default(
    pg_schema: str,
) -> None:
    """The lock order is reasoned out for READ COMMITTED, so the store pins it."""
    serializable = make_conninfo(
        pg_schema, options="-c default_transaction_isolation=serializable"
    )

    async with (
        PostgresStore(serializable) as store,
        store.pool.connection() as conn,
        conn.transaction(),
    ):
        cursor = await conn.execute("SHOW transaction_isolation")
        row = await cursor.fetchone()

    assert row is not None and row["transaction_isolation"] == "read committed"


for _name in _NOT_YET:
    setattr(TestPostgresStore, _name, _until_step_3(getattr(StoreContract, _name)))


def _flow(name: str, version: str) -> StoredFlow:
    content: JsonValue = {
        "name": name,
        "version": version,
        "steps": {"work": {"handler": "tasks:work"}},
    }
    return StoredFlow(parse_flow(content), content)


async def test_an_upload_spares_a_tree_whose_run_of_the_flow_just_succeeded(
    pg_flow_store: PostgresStore,
) -> None:
    """Two transactions interleaved: a sub-run succeeds as its flow is uploaded.

    The upload reads the trees to cancel, then waits for the root lock the
    succeeding transaction holds. Once it has the lock, nothing in the tree runs
    the flow any more, so the tree must stay active, as one order or the other
    of the two operations would leave it.
    """
    store = pg_flow_store
    await store.store_flows([_flow("a", "1.0.0"), _flow("b", "1.0.0")])
    root = await store.start_run("a", Version(1, 0, 0), {})
    sub_run = await store.start_run(
        "b", Version(1, 0, 0), {}, parent_id=root.id, parent_address=Address("call")
    )

    async with store.pool.connection() as succeeding, succeeding.transaction():
        # succeed_run(sub_run), by hand, stopped short of its commit.
        await succeeding.execute(
            f"SELECT id FROM {RUNS_TABLE} WHERE id = %s FOR UPDATE", (root.id,)
        )
        await succeeding.execute(
            f"UPDATE {RUNS_TABLE} SET status = 'succeeded' WHERE id = %s",
            (sub_run.id,),
        )
        uploading = asyncio.create_task(store.store_flows([_flow("b", "2.0.0")]))
        await asyncio.sleep(0.2)
        assert not uploading.done()  # waiting on the root lock

    assert await uploading == [True]
    assert (await store.get_run(root.id)).status is RunStatus.ACTIVE
    assert (await store.get_run(sub_run.id)).status is RunStatus.SUCCEEDED
