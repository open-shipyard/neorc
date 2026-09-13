# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The HTTP client against the real manager application.

Both halves are exercised together, over an in-process ASGI transport: no
server, no port, but the same routing, serialisation and status codes a worker
meets in a deployment.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from neorc.http import HttpQueueClient
from neorc.manager import create_app
from neorc_core import (
    Manager,
    ManagerUnavailableError,
    TaskNotFoundError,
    TaskStateError,
    TaskStatus,
)


@pytest.fixture
async def client(manager: Manager) -> AsyncIterator[HttpQueueClient]:
    app = create_app(manager, long_poll_timeout=2)
    transport = httpx.ASGITransport(app=app)
    async with HttpQueueClient("manager.test", transport=transport) as client:
        yield client


async def test_a_published_task_can_be_read_back(
    client: HttpQueueClient, manager: Manager
) -> None:
    task_id = await client.publish("send_email", {"to": "a@example.com"}, priority=2)

    task = await client.get_task(task_id)

    assert task.id == task_id
    assert task.name == "send_email"
    assert task.payload == {"to": "a@example.com"}
    assert task.priority == 2
    assert task.status is TaskStatus.PENDING
    assert await manager.get_status(task_id) is TaskStatus.PENDING


async def test_a_worker_picks_up_what_a_publisher_sent(
    client: HttpQueueClient,
) -> None:
    task_id = await client.publish("send_email", {"n": 1})

    picked = await client.pick_next_task(timeout=1, lease_seconds=30)

    assert picked is not None
    assert picked.id == task_id
    assert picked.status is TaskStatus.CLAIMED
    assert picked.attempts == 1
    assert picked.lease_expires_at is not None


async def test_an_empty_queue_answers_nothing_rather_than_failing(
    client: HttpQueueClient,
) -> None:
    assert await client.pick_next_task(timeout=0) is None


async def test_the_whole_lifecycle_over_http(client: HttpQueueClient) -> None:
    task_id = await client.publish("send_email", {})
    await client.pick_next_task(timeout=1)

    await client.report_started(task_id)
    assert await client.get_status(task_id) is TaskStatus.RUNNING

    expires_at = await client.extend_lease(task_id, lease_seconds=45)
    assert expires_at > datetime.now(UTC)

    await client.report_finished(task_id)
    assert await client.get_status(task_id) is TaskStatus.SUCCEEDED


async def test_a_failure_reason_survives_the_round_trip(
    client: HttpQueueClient,
) -> None:
    task_id = await client.publish("send_email", {})
    await client.pick_next_task(timeout=1)

    await client.report_finished(task_id, error="ValueError: nope")
    task = await client.get_task(task_id)

    assert task.status is TaskStatus.FAILED
    assert task.error == "ValueError: nope"


async def test_a_deferred_task_keeps_its_time_across_the_wire(
    client: HttpQueueClient,
) -> None:
    run_after = datetime.now(UTC) + timedelta(hours=2)

    task_id = await client.publish("later", {}, run_after=run_after)
    task = await client.get_task(task_id)

    assert task.run_after == run_after
    assert await client.pick_next_task(timeout=0) is None


async def test_a_missing_task_reads_as_missing_not_as_a_500(
    client: HttpQueueClient,
) -> None:
    with pytest.raises(TaskNotFoundError):
        await client.get_task(uuid.uuid4())


async def test_a_refused_transition_reads_as_a_state_error(
    client: HttpQueueClient,
) -> None:
    task_id = await client.publish("send_email", {})

    with pytest.raises(TaskStateError):
        await client.report_started(task_id)


async def test_an_unreachable_manager_is_reported_as_such() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(refuse)
    async with HttpQueueClient("127.0.0.1:1", transport=transport) as client:
        with pytest.raises(ManagerUnavailableError, match="connection refused"):
            await client.publish("send_email", {})


async def test_a_long_poll_returns_as_soon_as_a_task_is_published(
    client: HttpQueueClient,
) -> None:
    async def publish_shortly() -> None:
        await asyncio.sleep(0.05)
        await client.publish("send_email", {})

    loop = asyncio.get_running_loop()
    started = loop.time()
    async with asyncio.TaskGroup() as group:
        group.create_task(publish_shortly())
        picked = await client.pick_next_task(timeout=5)

    assert picked is not None
    assert loop.time() - started < 2


async def test_the_manager_caps_a_long_poll_at_its_own_deadline(
    client: HttpQueueClient,
) -> None:
    """A worker asking for an hour is answered at the manager's limit, not its own."""
    loop = asyncio.get_running_loop()
    started = loop.time()

    picked = await client.pick_next_task(timeout=3600)

    assert picked is None
    assert loop.time() - started < 10


async def test_a_bare_host_and_a_url_address_the_same_manager(
    manager: Manager,
) -> None:
    app = create_app(manager)
    transport = httpx.ASGITransport(app=app)

    async with (
        HttpQueueClient("manager.test:8420", transport=transport) as bare,
        HttpQueueClient("http://manager.test:8420", transport=transport) as full,
    ):
        assert await bare.publish("a", {}) != await full.publish("b", {})
