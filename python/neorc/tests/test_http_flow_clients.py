# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The HTTP flow clients against the real application, over an ASGI transport.

Both clients are held to the client contracts, on the memory store, with the
manager's routes in between: no server, no port, but the same routing,
serialisation and status codes a deployment meets. The tests here cover what
only HTTP adds: an unreachable manager, an answer that is not the manager's,
and the manager's long-poll deadline.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest

from neorc.http import HttpFlowQueueClient, HttpManagerClient
from neorc.manager import create_app
from neorc_core import (
    FlowManager,
    FlowQueueClient,
    Manager,
    ManagerClient,
    ManagerUnavailableError,
)
from neorc_core.local import MemoryStore, MemoryTaskNotifier
from neorc_core.testing.contracts import (
    FlowQueueClientContract,
    ManagerClientContract,
)


@pytest.fixture
def flows() -> FlowManager:
    return FlowManager(
        MemoryStore(), tasks=MemoryTaskNotifier(), events=MemoryTaskNotifier()
    )


@pytest.fixture
def transport(manager: Manager, flows: FlowManager) -> httpx.ASGITransport:
    return httpx.ASGITransport(
        app=create_app(manager, flows=flows, long_poll_timeout=2)
    )


@pytest.fixture
async def manager_client(
    transport: httpx.ASGITransport,
) -> AsyncIterator[HttpManagerClient]:
    async with HttpManagerClient("manager.test", transport=transport) as client:
        yield client


@pytest.fixture
async def queue_client(
    transport: httpx.ASGITransport,
) -> AsyncIterator[HttpFlowQueueClient]:
    async with HttpFlowQueueClient("manager.test", transport=transport) as client:
        yield client


class TestHttpManagerClient(ManagerClientContract):
    @pytest.fixture
    def manager_client(self, manager_client: HttpManagerClient) -> ManagerClient:
        return manager_client

    @pytest.fixture
    def queue_client(self, queue_client: HttpFlowQueueClient) -> FlowQueueClient:
        return queue_client


class TestHttpFlowQueueClient(FlowQueueClientContract):
    @pytest.fixture
    def manager_client(self, manager_client: HttpManagerClient) -> ManagerClient:
        return manager_client

    @pytest.fixture
    def queue_client(self, queue_client: HttpFlowQueueClient) -> FlowQueueClient:
        return queue_client


async def test_an_unreachable_manager_is_reported_as_such() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(refuse)
    async with (
        HttpManagerClient("127.0.0.1:1", transport=transport) as manager_client,
        HttpFlowQueueClient("127.0.0.1:1", transport=transport) as queue_client,
    ):
        with pytest.raises(ManagerUnavailableError, match="connection refused"):
            await manager_client.upload_flows([])
        with pytest.raises(ManagerUnavailableError, match="connection refused"):
            await queue_client.task_definitions("default")


async def test_an_answer_that_is_not_the_managers_is_unavailability() -> None:
    """A proxy's error page names no core error: it is not the manager talking."""

    def gateway(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>Bad Gateway</html>")

    async with HttpManagerClient(
        "manager.test", transport=httpx.MockTransport(gateway)
    ) as client:
        with pytest.raises(ManagerUnavailableError, match="502"):
            await client.upload_flows([])


async def test_a_success_that_is_not_the_managers_is_unavailability() -> None:
    """A health endpoint's 200 {} must not end a worker or scheduler loop."""

    def something_else(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.MockTransport(something_else)
    async with (
        HttpManagerClient("manager.test", transport=transport) as manager_client,
        HttpFlowQueueClient("manager.test", transport=transport) as queue_client,
    ):
        for call in (
            manager_client.upload_flows([]),
            manager_client.get_run(uuid.uuid4()),
            manager_client.wait_for_events(0, timeout=0),
            queue_client.task_definitions("default"),
            queue_client.pick_next_task("default", timeout=0),
            queue_client.extend_lease(uuid.uuid4()),
        ):
            with pytest.raises(ManagerUnavailableError, match="unexpected body"):
                await call


@pytest.mark.parametrize("who", ["scheduler", "worker"])
async def test_the_manager_caps_a_long_poll_at_its_own_deadline(
    manager_client: HttpManagerClient, queue_client: HttpFlowQueueClient, who: str
) -> None:
    """A poll asking for an hour is answered at the manager's limit, not its own."""
    loop = asyncio.get_running_loop()
    begun = loop.time()

    if who == "scheduler":
        assert await manager_client.wait_for_events(0, timeout=3600) == []
    else:
        assert await queue_client.pick_next_task("default", timeout=3600) is None

    assert loop.time() - begun < 10
