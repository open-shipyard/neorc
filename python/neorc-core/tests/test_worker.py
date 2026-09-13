# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The worker loop, run against a real manager over an in-process queue client."""

import asyncio

import pytest

from neorc_core import (
    Manager,
    ManagerUnavailableError,
    Task,
    TaskId,
    TaskStatus,
    Worker,
)
from neorc_core.testing import DirectQueueClient, MemoryTaskNotifier, MemoryTaskStore


@pytest.fixture
def queue_client(manager: Manager) -> DirectQueueClient:
    return DirectQueueClient(manager)


@pytest.fixture
def worker(queue_client: DirectQueueClient) -> Worker:
    return Worker("in-process", queue_client, poll_timeout=0.1, lease_seconds=1)


async def test_a_handled_task_succeeds(
    manager: Manager, worker: Worker, queue_client: DirectQueueClient
) -> None:
    seen: list[Task] = []

    async def handle(task: Task) -> None:
        seen.append(task)

    worker.register("send_email", handle)
    task_id = await queue_client.publish("send_email", {"to": "a@example.com"})

    executed = await worker.run_once()

    assert executed is not None
    assert [task.payload for task in seen] == [{"to": "a@example.com"}]
    assert await manager.get_status(task_id) is TaskStatus.SUCCEEDED


async def test_a_raising_handler_fails_the_task_with_its_reason(
    manager: Manager, worker: Worker, queue_client: DirectQueueClient
) -> None:
    async def handle(task: Task) -> None:
        raise ValueError("no recipient")

    worker.register("send_email", handle)
    task_id = await queue_client.publish("send_email", {})

    await worker.run_once()

    task = await manager.get_task(task_id)
    assert task.status is TaskStatus.FAILED
    assert task.error == "ValueError: no recipient"


async def test_a_task_nobody_handles_fails_rather_than_hanging(
    manager: Manager, worker: Worker, queue_client: DirectQueueClient
) -> None:
    task_id = await queue_client.publish("unknown_kind", {})

    await worker.run_once()

    task = await manager.get_task(task_id)
    assert task.status is TaskStatus.FAILED
    assert task.error is not None
    assert "no handler" in task.error


async def test_an_idle_worker_returns_nothing(worker: Worker) -> None:
    assert await worker.run_once() is None


async def test_a_long_task_keeps_its_lease_by_heartbeating(
    manager: Manager, queue_client: DirectQueueClient
) -> None:
    worker = Worker("in-process", queue_client, poll_timeout=0.1, lease_seconds=0.15)
    other_worker_saw: list[Task | None] = []

    async def slow(task: Task) -> None:
        # Four times the lease: without heartbeats the task would be stolen.
        await asyncio.sleep(0.6)
        other_worker_saw.append(await manager.pick_next_task(timeout=0))

    worker.register("slow", slow)
    task_id = await queue_client.publish("slow", {})

    await worker.run_once()

    assert other_worker_saw == [None]
    assert await manager.get_status(task_id) is TaskStatus.SUCCEEDED


async def test_a_task_whose_worker_stops_beating_is_handed_on(
    manager: Manager, queue_client: DirectQueueClient
) -> None:
    claimed = await manager.publish("orphan", {})
    leased = await manager.pick_next_task(timeout=0, lease_seconds=0.05)
    assert leased is not None and leased.id == claimed.id

    await asyncio.sleep(0.06)
    reclaimed = await manager.pick_next_task(timeout=0)

    assert reclaimed is not None
    assert reclaimed.id == claimed.id
    assert reclaimed.attempts == 2


async def test_registering_a_name_twice_is_refused(worker: Worker) -> None:
    async def handle(task: Task) -> None:
        return None

    worker.register("send_email", handle)

    with pytest.raises(ValueError, match="already registered"):
        worker.register("send_email", handle)


async def test_run_drains_tasks_until_stopped(
    manager: Manager, worker: Worker, queue_client: DirectQueueClient
) -> None:
    done = asyncio.Event()
    handled: list[TaskId] = []

    async def handle(task: Task) -> None:
        handled.append(task.id)
        if len(handled) == 3:
            done.set()

    worker.register("send_email", handle)
    for _ in range(3):
        await queue_client.publish("send_email", {})

    running = asyncio.create_task(worker.run())
    await asyncio.wait_for(done.wait(), timeout=5)
    worker.stop()
    await asyncio.wait_for(running, timeout=5)

    assert len(handled) == 3


async def test_a_worker_survives_an_unreachable_manager() -> None:
    store = MemoryTaskStore()
    manager = Manager(store, MemoryTaskNotifier())
    client = DirectQueueClient(manager)
    failures = 0

    async def flaky(*, timeout: float, lease_seconds: float = 60.0) -> Task | None:
        nonlocal failures
        failures += 1
        if failures <= 2:
            raise ManagerUnavailableError("connection refused")
        return await manager.pick_next_task(
            timeout=timeout, lease_seconds=lease_seconds
        )

    client.pick_next_task = flaky  # type: ignore[method-assign]
    worker = Worker("in-process", client, poll_timeout=0.05)
    handled = asyncio.Event()

    async def handle(task: Task) -> None:
        handled.set()

    worker.register("send_email", handle)
    await manager.publish("send_email", {})

    running = asyncio.create_task(worker.run())
    await asyncio.wait_for(handled.wait(), timeout=10)
    worker.stop()
    await asyncio.wait_for(running, timeout=10)

    assert failures >= 3


async def test_stopping_an_idle_worker_does_not_wait_out_its_poll(
    queue_client: DirectQueueClient,
) -> None:
    """A supervisor's grace period is shorter than a long poll."""
    worker = Worker("in-process", queue_client, poll_timeout=30)
    running = asyncio.create_task(worker.run())
    await asyncio.sleep(0.1)  # settle into the poll

    loop = asyncio.get_running_loop()
    started = loop.time()
    worker.stop()
    await asyncio.wait_for(running, timeout=5)

    assert loop.time() - started < 1


async def test_stopping_lets_the_task_in_hand_finish(
    manager: Manager, queue_client: DirectQueueClient
) -> None:
    worker = Worker("in-process", queue_client, poll_timeout=30, lease_seconds=5)
    started = asyncio.Event()
    finished = False

    async def slow(task: Task) -> None:
        nonlocal finished
        started.set()
        await asyncio.sleep(0.3)
        finished = True

    worker.register("slow", slow)
    task_id = await queue_client.publish("slow", {})
    running = asyncio.create_task(worker.run())

    await asyncio.wait_for(started.wait(), timeout=5)
    worker.stop()
    await asyncio.wait_for(running, timeout=5)

    assert finished is True
    assert await manager.get_status(task_id) is TaskStatus.SUCCEEDED
