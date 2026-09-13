# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The manager's behaviour: leasing, ordering, transitions, waiting."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from neorc_core import Manager, TaskNotFoundError, TaskStateError, TaskStatus
from neorc_core.local import MemoryTaskNotifier, MemoryTaskStore


async def test_published_task_starts_pending(manager: Manager) -> None:
    task = await manager.publish("send_email", {"to": "a@example.com"})

    assert task.status is TaskStatus.PENDING
    assert task.attempts == 0
    assert task.lease_expires_at is None
    assert task.payload == {"to": "a@example.com"}


async def test_pick_leases_the_task_and_counts_the_attempt(manager: Manager) -> None:
    await manager.publish("send_email", {})

    claimed = await manager.pick_next_task(timeout=0, lease_seconds=30)

    assert claimed is not None
    assert claimed.status is TaskStatus.CLAIMED
    assert claimed.attempts == 1
    assert claimed.lease_expires_at is not None
    assert claimed.lease_expires_at > datetime.now(UTC)


async def test_a_leased_task_is_not_handed_to_a_second_worker(
    manager: Manager,
) -> None:
    await manager.publish("send_email", {})

    first = await manager.pick_next_task(timeout=0)
    second = await manager.pick_next_task(timeout=0)

    assert first is not None
    assert second is None


async def test_concurrent_picks_never_share_a_task(manager: Manager) -> None:
    for _ in range(5):
        await manager.publish("send_email", {})

    picked = await asyncio.gather(
        *(manager.pick_next_task(timeout=0) for _ in range(20))
    )

    ids = [task.id for task in picked if task is not None]
    assert len(ids) == 5
    assert len(set(ids)) == 5


async def test_higher_priority_goes_first(manager: Manager) -> None:
    await manager.publish("low", {}, priority=0)
    await manager.publish("high", {}, priority=10)

    first = await manager.pick_next_task(timeout=0)

    assert first is not None
    assert first.name == "high"


async def test_a_deferred_task_is_not_picked_before_its_time(
    manager: Manager,
) -> None:
    await manager.publish("later", {}, run_after=datetime.now(UTC) + timedelta(days=1))

    assert await manager.pick_next_task(timeout=0) is None


async def test_a_lapsed_lease_returns_the_task_to_the_queue(
    manager: Manager,
) -> None:
    await manager.publish("send_email", {})
    first = await manager.pick_next_task(timeout=0, lease_seconds=0.05)
    assert first is not None

    await asyncio.sleep(0.06)
    second = await manager.pick_next_task(timeout=0)

    assert second is not None
    assert second.id == first.id
    assert second.attempts == 2


async def test_a_heartbeat_holds_the_task(manager: Manager) -> None:
    await manager.publish("send_email", {})
    first = await manager.pick_next_task(timeout=0, lease_seconds=0.05)
    assert first is not None

    await asyncio.sleep(0.03)
    await manager.extend_lease(first.id, lease_seconds=5)
    await asyncio.sleep(0.04)

    assert await manager.pick_next_task(timeout=0) is None


async def test_lifecycle_reaches_succeeded(manager: Manager) -> None:
    published = await manager.publish("send_email", {})
    await manager.pick_next_task(timeout=0)
    await manager.report_started(published.id)
    await manager.report_finished(published.id)

    task = await manager.get_task(published.id)
    assert task.status is TaskStatus.SUCCEEDED
    assert task.error is None
    assert task.lease_expires_at is None


async def test_a_failure_keeps_its_reason(manager: Manager) -> None:
    published = await manager.publish("send_email", {})
    await manager.pick_next_task(timeout=0)
    await manager.report_finished(published.id, error="ValueError: nope")

    task = await manager.get_task(published.id)
    assert task.status is TaskStatus.FAILED
    assert task.error == "ValueError: nope"


async def test_a_pending_task_cannot_be_started(manager: Manager) -> None:
    published = await manager.publish("send_email", {})

    with pytest.raises(TaskStateError):
        await manager.report_started(published.id)


async def test_a_finished_task_cannot_finish_again(manager: Manager) -> None:
    published = await manager.publish("send_email", {})
    await manager.pick_next_task(timeout=0)
    await manager.report_finished(published.id)

    with pytest.raises(TaskStateError):
        await manager.report_finished(published.id, error="too late")


async def test_reporting_a_start_twice_is_allowed(manager: Manager) -> None:
    published = await manager.publish("send_email", {})
    await manager.pick_next_task(timeout=0)
    await manager.report_started(published.id)
    await manager.report_started(published.id)

    assert await manager.get_status(published.id) is TaskStatus.RUNNING


async def test_an_unknown_task_is_reported_as_missing(manager: Manager) -> None:
    with pytest.raises(TaskNotFoundError):
        await manager.get_task(uuid.uuid4())


async def test_a_lease_cannot_be_extended_on_a_finished_task(
    manager: Manager,
) -> None:
    published = await manager.publish("send_email", {})
    await manager.pick_next_task(timeout=0)
    await manager.report_finished(published.id)

    with pytest.raises(TaskStateError):
        await manager.extend_lease(published.id)


async def test_waiting_ends_when_a_task_is_published(manager: Manager) -> None:
    async def publish_shortly() -> None:
        await asyncio.sleep(0.05)
        await manager.publish("send_email", {})

    async with asyncio.TaskGroup() as group:
        group.create_task(publish_shortly())
        picked = await manager.pick_next_task(timeout=5)

    assert picked is not None


async def test_waiting_gives_up_at_the_deadline(manager: Manager) -> None:
    started = asyncio.get_running_loop().time()

    picked = await manager.pick_next_task(timeout=0.1)

    assert picked is None
    assert asyncio.get_running_loop().time() - started >= 0.1


async def test_a_task_published_before_the_wait_is_not_slept_through() -> None:
    """The lost-wakeup case: the task lands between the claim and the wait."""
    store = MemoryTaskStore()
    notifier = MemoryTaskNotifier()
    manager = Manager(store, notifier)
    claims = 0

    original_claim_next = store.claim_next

    async def claim_then_publish(*, lease_seconds: float = 60.0) -> object:
        nonlocal claims
        result = await original_claim_next(lease_seconds=lease_seconds)
        claims += 1
        if claims == 1:
            # Exactly the race: published after the claim found nothing, before
            # the caller starts waiting.
            await manager.publish("send_email", {})
        return result

    store.claim_next = claim_then_publish  # type: ignore[assignment,method-assign]

    picked = await asyncio.wait_for(manager.pick_next_task(timeout=5), timeout=1)

    assert picked is not None
