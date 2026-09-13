# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""LISTEN/NOTIFY, against a real server.

What matters here: a waiter costs no connection, an announcement from another
manager process still wakes it, and nothing published in the gap between a
claim and a wait is slept through.
"""

from __future__ import annotations

import asyncio

import pytest

from neorc.postgres import PostgresTaskNotifier, PostgresTaskStore
from neorc_core import Manager

pytestmark = pytest.mark.postgres


async def test_a_waiter_is_woken_by_an_announcement(
    pg_notifier: PostgresTaskNotifier,
) -> None:
    async with pg_notifier.subscribe() as subscription:

        async def announce() -> None:
            await asyncio.sleep(0.05)
            await pg_notifier.notify()

        async with asyncio.TaskGroup() as group:
            group.create_task(announce())
            woken = await subscription.wait(timeout=5)

    assert woken is True


async def test_a_waiter_gives_up_at_its_timeout(
    pg_notifier: PostgresTaskNotifier,
) -> None:
    async with pg_notifier.subscribe() as subscription:
        assert await subscription.wait(timeout=0.1) is False


async def test_an_announcement_between_waits_is_not_lost(
    pg_notifier: PostgresTaskNotifier,
) -> None:
    """The lost-wakeup case: it arrives while the caller is off claiming."""
    async with pg_notifier.subscribe() as subscription:
        await pg_notifier.notify()
        await asyncio.sleep(0.1)  # the announcement lands with nobody waiting

        assert await subscription.wait(timeout=0.5) is True


async def test_a_task_published_on_another_process_wakes_this_one(
    database_url: str, pg_notifier: PostgresTaskNotifier
) -> None:
    """Two manager processes share one channel, so one wakes the other's workers."""
    async with (
        PostgresTaskNotifier(database_url) as elsewhere,
        pg_notifier.subscribe() as subscription,
    ):

        async def announce() -> None:
            await asyncio.sleep(0.05)
            await elsewhere.notify()

        async with asyncio.TaskGroup() as group:
            group.create_task(announce())
            woken = await subscription.wait(timeout=5)

    assert woken is True


async def test_many_waiters_cost_no_connections(
    database_url: str, pg_store: PostgresTaskStore, pg_notifier: PostgresTaskNotifier
) -> None:
    """Fifty idle workers on a pool of two, which is the whole point."""
    async with PostgresTaskStore(database_url, min_size=1, max_size=2) as small_pool:
        manager = Manager(small_pool, pg_notifier)
        waiting = [
            asyncio.create_task(manager.pick_next_task(timeout=2)) for _ in range(50)
        ]
        await asyncio.sleep(0.2)  # let them all reach the wait

        for _ in range(3):
            await manager.publish("send_email", {})

        done, pending = await asyncio.wait(waiting, timeout=10)
        picked = [task.result() for task in done if task.result() is not None]
        for task in pending:
            task.cancel()

    assert len(picked) == 3


async def test_a_waiting_worker_is_woken_by_a_publish(
    pg_store: PostgresTaskStore, pg_notifier: PostgresTaskNotifier
) -> None:
    manager = Manager(pg_store, pg_notifier)

    async def publish_shortly() -> None:
        await asyncio.sleep(0.05)
        await manager.publish("send_email", {})

    loop = asyncio.get_running_loop()
    started = loop.time()
    async with asyncio.TaskGroup() as group:
        group.create_task(publish_shortly())
        picked = await manager.pick_next_task(timeout=10)

    assert picked is not None
    # Woken by the announcement, not by falling out of the poll timeout.
    assert loop.time() - started < 2
