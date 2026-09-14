# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The whole thing: a manager on a socket, Postgres underneath, workers over HTTP.

Nothing here is stubbed. If this passes, the pieces fit: a publisher enqueues
over HTTP, the manager stores in Postgres, workers on their own clients claim
without collisions, and the work comes back done. The example scenarios of
``neorc_core.testing.examples`` run here as they run in memory, with a
scheduler and one worker per queue on the HTTP clients.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import uvicorn

from neorc.http import HttpFlowQueueClient, HttpManagerClient, HttpQueueClient
from neorc.manager import build_app
from neorc_core import FlowWorker, Scheduler, Task, TaskStatus, Worker
from neorc_core.testing import examples

pytestmark = pytest.mark.postgres

EXAMPLES = Path(__file__).parents[3] / "examples"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@pytest.fixture
async def manager_address(pg_schema: str) -> AsyncIterator[str]:
    """A manager service listening on a real port, backed by a real database."""
    port = _free_port()
    app = build_app(pg_schema, create_schema=True, long_poll_timeout=2)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"
    )
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(20):
            while not server.started:
                await asyncio.sleep(0.05)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(20):
                await serving


async def test_a_published_task_is_run_by_a_worker(manager_address: str) -> None:
    done = asyncio.Event()
    seen: list[Task] = []

    async def handle(task: Task) -> None:
        seen.append(task)
        done.set()

    async with (
        HttpQueueClient(manager_address) as publisher,
        HttpQueueClient(manager_address) as worker_client,
    ):
        worker = Worker(manager_address, worker_client, poll_timeout=2, lease_seconds=5)
        worker.register("greet", handle)
        running = asyncio.create_task(worker.run())

        task_id = await publisher.publish("greet", {"name": "world"})
        await asyncio.wait_for(done.wait(), timeout=20)
        worker.stop()
        await asyncio.wait_for(running, timeout=20)

        assert [task.payload for task in seen] == [{"name": "world"}]
        assert await publisher.get_status(task_id) is TaskStatus.SUCCEEDED


async def test_three_workers_share_a_batch_without_doubling_up(
    manager_address: str,
) -> None:
    task_count = 12
    handled: list[str] = []
    all_done = asyncio.Event()

    async def handle(task: Task) -> None:
        # Real work takes a moment; that is when a second worker would steal it.
        await asyncio.sleep(0.05)
        handled.append(task.payload["item"])
        if len(handled) == task_count:
            all_done.set()

    async with contextlib.AsyncExitStack() as stack:
        publisher = await stack.enter_async_context(HttpQueueClient(manager_address))
        workers = []
        for _ in range(3):
            client = await stack.enter_async_context(HttpQueueClient(manager_address))
            worker = Worker(manager_address, client, poll_timeout=2, lease_seconds=5)
            worker.register("process", handle)
            workers.append(worker)

        running = [asyncio.create_task(worker.run()) for worker in workers]
        task_ids = [
            await publisher.publish("process", {"item": f"item-{index}"})
            for index in range(task_count)
        ]

        await asyncio.wait_for(all_done.wait(), timeout=60)
        for worker in workers:
            worker.stop()
        await asyncio.wait_for(asyncio.gather(*running), timeout=30)

        assert sorted(handled) == sorted(f"item-{index}" for index in range(task_count))
        statuses = [await publisher.get_status(task_id) for task_id in task_ids]
        assert statuses == [TaskStatus.SUCCEEDED] * task_count


async def test_a_task_outliving_its_lease_is_held_by_heartbeats(
    manager_address: str,
) -> None:
    """The lease is short, the work is long, and no second worker gets a look in."""
    started = asyncio.Event()
    finished = asyncio.Event()

    async def slow(task: Task) -> None:
        started.set()
        await asyncio.sleep(2)
        finished.set()

    async with (
        HttpQueueClient(manager_address) as publisher,
        HttpQueueClient(manager_address) as first_client,
        HttpQueueClient(manager_address) as second_client,
    ):
        first = Worker(manager_address, first_client, poll_timeout=1, lease_seconds=0.6)
        first.register("slow", slow)
        running = asyncio.create_task(first.run())

        task_id = await publisher.publish("slow", {})
        await asyncio.wait_for(started.wait(), timeout=20)

        # Long enough for several lapsed leases, if the heartbeat were not there.
        await asyncio.sleep(1.5)
        stolen = await second_client.pick_next_task(timeout=0)

        await asyncio.wait_for(finished.wait(), timeout=20)
        first.stop()
        await asyncio.wait_for(running, timeout=20)

        assert stolen is None
        assert await publisher.get_status(task_id) is TaskStatus.SUCCEEDED


async def test_a_failing_task_is_recorded_with_its_reason(
    manager_address: str,
) -> None:
    failed = asyncio.Event()

    async def explode(task: Task) -> None:
        failed.set()
        raise RuntimeError("the widget jammed")

    async with (
        HttpQueueClient(manager_address) as publisher,
        HttpQueueClient(manager_address) as worker_client,
    ):
        worker = Worker(manager_address, worker_client, poll_timeout=2, lease_seconds=5)
        worker.register("explode", explode)
        running = asyncio.create_task(worker.run())

        task_id = await publisher.publish("explode", {})
        await asyncio.wait_for(failed.wait(), timeout=20)
        await asyncio.sleep(0.5)
        worker.stop()
        await asyncio.wait_for(running, timeout=20)

        task = await publisher.get_task(task_id)
        assert task.status is TaskStatus.FAILED
        assert task.error == "RuntimeError: the widget jammed"


async def test_a_worker_waiting_on_an_empty_queue_starts_the_moment_work_arrives(
    manager_address: str,
) -> None:
    """The long poll plus LISTEN/NOTIFY: latency should be a hop, not a poll."""
    done = asyncio.Event()

    async def handle(task: Task) -> None:
        done.set()

    async with (
        HttpQueueClient(manager_address) as publisher,
        HttpQueueClient(manager_address) as worker_client,
    ):
        worker = Worker(
            manager_address, worker_client, poll_timeout=30, lease_seconds=5
        )
        worker.register("greet", handle)
        running = asyncio.create_task(worker.run())
        await asyncio.sleep(0.3)  # let it settle into the wait

        loop = asyncio.get_running_loop()
        started = loop.time()
        await publisher.publish("greet", {})
        await asyncio.wait_for(done.wait(), timeout=20)
        elapsed = loop.time() - started

        worker.stop()
        await asyncio.wait_for(running, timeout=40)

        assert elapsed < 2


# The examples, deployed.


@pytest.fixture
def own_tasks_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each example has a ``tasks`` module: import this one's, not another's."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "tasks", raising=False)
    yield
    sys.modules.pop("tasks", None)


@contextlib.asynccontextmanager
async def deployed(
    manager_address: str, example: str, queues: list[str]
) -> AsyncIterator[HttpManagerClient]:
    """A scheduler and a worker per queue on the HTTP clients, for the block."""
    async with contextlib.AsyncExitStack() as stack:
        client = await stack.enter_async_context(
            HttpManagerClient(manager_address, poll_timeout=2)
        )
        scheduler = Scheduler(
            await stack.enter_async_context(
                HttpManagerClient(manager_address, poll_timeout=2)
            ),
            poll_timeout=2,
        )
        workers = [
            FlowWorker(
                await stack.enter_async_context(
                    HttpFlowQueueClient(manager_address, poll_timeout=2)
                ),
                queue=queue,
                code_location=EXAMPLES / example,
                poll_timeout=2,
                lease_seconds=5,
            )
            for queue in queues
        ]
        running = [asyncio.create_task(scheduler.run())]
        running += [asyncio.create_task(worker.run()) for worker in workers]
        try:
            yield client
        finally:
            scheduler.stop()
            for worker in workers:
                worker.stop()
            await asyncio.wait_for(
                asyncio.gather(*running, return_exceptions=True), timeout=30
            )


@pytest.mark.usefixtures("own_tasks_module")
@pytest.mark.parametrize("flow", ["a", "b"])
async def test_hello_runs_deployed(
    manager_address: str, flow: str, capfd: pytest.CaptureFixture[str]
) -> None:
    async with deployed(manager_address, "hello", ["default"]) as client:
        await examples.hello(client, EXAMPLES, flow)

    assert capfd.readouterr().out == f"{flow}\n"


@pytest.mark.usefixtures("own_tasks_module")
async def test_word_picker_runs_deployed(manager_address: str) -> None:
    async with deployed(manager_address, "wordplay", ["default", "scoring"]) as client:
        await examples.word_picker(client, EXAMPLES)


@pytest.mark.usefixtures("own_tasks_module")
async def test_word_picker_rounds_runs_deployed(manager_address: str) -> None:
    async with deployed(manager_address, "wordplay", ["default", "scoring"]) as client:
        await examples.word_picker_rounds(client, EXAMPLES)
