# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The HTTP clients against the real application, over an ASGI transport.

Both clients are held to the client contracts, on the memory store, with the
manager's routes in between: no server, no port, but the same routing,
serialisation and status codes a deployment meets. The tests here cover what
only HTTP adds: an unreachable manager, an answer that is not the manager's,
and the manager's long-poll deadline.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from neorc.http import HttpManagerClient, HttpQueueClient
from neorc.manager import create_app
from neorc_core import (
    Access,
    AuthenticationError,
    InvalidValueError,
    Manager,
    ManagerClient,
    ManagerUnavailableError,
    PermissionDeniedError,
    QueueClient,
    Role,
    TaskNotFoundError,
)
from neorc_core.local import MemoryCredentialStore
from neorc_core.testing.contracts import (
    ManagerClientContract,
    QueueClientContract,
)


@pytest.fixture
def transport(manager: Manager) -> httpx.ASGITransport:
    return httpx.ASGITransport(
        app=create_app(manager, access=None, long_poll_timeout=2)
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
) -> AsyncIterator[HttpQueueClient]:
    async with HttpQueueClient("manager.test", transport=transport) as client:
        yield client


class TestHttpManagerClient(ManagerClientContract):
    @pytest.fixture
    def manager_client(self, manager_client: HttpManagerClient) -> ManagerClient:
        return manager_client

    @pytest.fixture
    def queue_client(self, queue_client: HttpQueueClient) -> QueueClient:
        return queue_client


class TestHttpQueueClient(QueueClientContract):
    @pytest.fixture
    def manager_client(self, manager_client: HttpManagerClient) -> ManagerClient:
        return manager_client

    @pytest.fixture
    def queue_client(self, queue_client: HttpQueueClient) -> QueueClient:
        return queue_client


async def test_an_unreachable_manager_is_reported_as_such() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(refuse)
    async with (
        HttpManagerClient("127.0.0.1:1", transport=transport) as manager_client,
        HttpQueueClient("127.0.0.1:1", transport=transport) as queue_client,
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
        HttpQueueClient("manager.test", transport=transport) as queue_client,
    ):
        for call in (
            manager_client.upload_flows([]),
            manager_client.get_run(uuid.uuid4()),
            manager_client.wait_for_events(0, timeout=0),
            queue_client.task_definitions("default"),
            queue_client.receive_task("default", timeout=0),
            queue_client.extend_lease(uuid.uuid4()),
        ):
            with pytest.raises(ManagerUnavailableError, match="unexpected body"):
                await call


@pytest.mark.parametrize("who", ["scheduler", "worker"])
async def test_the_manager_caps_a_long_poll_at_its_own_deadline(
    manager_client: HttpManagerClient, queue_client: HttpQueueClient, who: str
) -> None:
    """A poll asking for an hour is answered at the manager's limit, not its own."""
    loop = asyncio.get_running_loop()
    begun = loop.time()

    if who == "scheduler":
        assert await manager_client.wait_for_events(0, timeout=3600) == []
    else:
        assert await queue_client.receive_task("default", timeout=3600) is None

    assert loop.time() - begun < 10


# With a token.


SENT = "neorc_sent-by-the-client"
"""The token both clients are given; ``RoleTokens`` checks every request has it."""

_WORKER_PATHS = re.compile(r"/(?:queues/(?P<queue>[^/]+)|tasks/(?P<task>[^/]+))/")


class RoleTokens(httpx.AsyncBaseTransport):
    """Sends each request on with a token of the role that may make it.

    A deployment gives each process a token of its own role; the contracts
    drive every request through two clients, so here the role is picked per
    request, as the manager's routes say: a worker's bound to the queue in the
    path, or the queue of the task named. Every request must arrive carrying
    the one token the clients were given.
    """

    def __init__(self, app: Any, manager: Manager, access: Access) -> None:
        self._inner = httpx.ASGITransport(app=app)
        self._manager = manager
        self._access = access
        self._tokens: dict[tuple[Role, str | None], str] = {}

    async def _token(self, role: Role, queue: str | None = None) -> str:
        if (role, queue) not in self._tokens:
            name = f"{role.value}-{queue or 'any'}"
            secret, _ = await self._access.create_token(name, role, queue=queue)
            self._tokens[role, queue] = secret
        return self._tokens[role, queue]

    async def _role_token(self, request: httpx.Request) -> str:
        path, method = request.url.path, request.method
        worker = _WORKER_PATHS.match(path + "/")
        if worker and worker["queue"]:
            return await self._token(Role.WORKER, worker["queue"])
        if worker and worker["task"] and method == "POST":
            try:
                task = await self._manager.get_task(uuid.UUID(worker["task"]))
                queue = task.queue
            except (ValueError, TaskNotFoundError):
                queue = "default"
            return await self._token(Role.WORKER, queue)
        if path == "/flows" and method == "POST":
            return await self._token(Role.CI)
        if (
            path.startswith("/runs/")
            and method == "POST"
            and not path.endswith("/cancel")
        ):
            return await self._token(Role.SCHEDULER)
        return await self._token(Role.USER)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == f"Bearer {SENT}"
        request.headers["authorization"] = f"Bearer {await self._role_token(request)}"
        return await self._inner.handle_async_request(request)


@pytest.fixture
async def secured(
    manager: Manager,
) -> AsyncIterator[tuple[httpx.AsyncBaseTransport, str]]:
    """A manager that needs a token of the right role for every request."""
    access = Access(MemoryCredentialStore())
    app = create_app(manager, access=access, long_poll_timeout=2)
    yield RoleTokens(app, manager, access), SENT


class TestHttpClientsWithATokenRequired(ManagerClientContract, QueueClientContract):
    """Every request of both clients carries the token, and every write is JSON.

    With a token of the role each request needs, as ``RoleTokens`` swaps in.
    """

    @pytest.fixture
    async def manager_client(
        self, secured: tuple[httpx.AsyncBaseTransport, str]
    ) -> AsyncIterator[ManagerClient]:
        transport, secret = secured
        async with HttpManagerClient(
            "https://manager.test", token=secret, transport=transport
        ) as client:
            yield client

    @pytest.fixture
    async def queue_client(
        self, secured: tuple[httpx.AsyncBaseTransport, str]
    ) -> AsyncIterator[QueueClient]:
        transport, secret = secured
        async with HttpQueueClient(
            "https://manager.test", token=secret, transport=transport
        ) as client:
            yield client


async def test_a_refused_token_raises_authentication_error(manager: Manager) -> None:
    access = Access(MemoryCredentialStore())
    transport = httpx.ASGITransport(app=create_app(manager, access=access))
    async with (
        HttpManagerClient(
            "https://manager.test", token="neorc_wrong", transport=transport
        ) as manager_client,
        HttpQueueClient("https://manager.test", transport=transport) as queue_client,
    ):
        with pytest.raises(AuthenticationError, match="unknown, revoked or expired"):
            await manager_client.wait_for_events(0, timeout=0)
        with pytest.raises(AuthenticationError, match="needs an API token"):
            await queue_client.receive_task("default", timeout=0)


async def test_a_request_the_role_may_not_make_raises_permission_denied(
    manager: Manager,
) -> None:
    access = Access(MemoryCredentialStore())
    transport = httpx.ASGITransport(app=create_app(manager, access=access))
    ci, _ = await access.create_token("ci", Role.CI)
    worker, _ = await access.create_token("w", Role.WORKER, queue="default")
    async with (
        HttpManagerClient(
            "https://manager.test", token=ci, transport=transport
        ) as manager_client,
        HttpQueueClient(
            "https://manager.test", token=worker, transport=transport
        ) as queue_client,
    ):
        with pytest.raises(PermissionDeniedError, match="a ci token may not"):
            await manager_client.wait_for_events(0, timeout=0)
        with pytest.raises(PermissionDeniedError, match="bound to queue 'default'"):
            await queue_client.receive_task("gpu", timeout=0)


@pytest.mark.parametrize(
    "address",
    ["manager.internal:8420", "http://10.0.0.5:8420", "http://example.com"],
)
def test_a_token_is_not_sent_over_plain_http_beyond_loopback(address: str) -> None:
    for client in (HttpManagerClient, HttpQueueClient):
        with pytest.raises(InvalidValueError, match=r"https://.*INSECURE_HTTP"):
            client(address, token="neorc_secret")


@pytest.mark.parametrize(
    "address",
    [
        "https://manager.internal:8420",
        "127.0.0.1:8420",
        "http://localhost:8420",
        "http://[::1]:8420",
        "http://api.localhost",
    ],
)
async def test_a_token_is_sent_over_https_or_to_loopback(address: str) -> None:
    seen: list[str | None] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(204)

    async with HttpManagerClient(
        address, token="neorc_secret", transport=httpx.MockTransport(record)
    ) as client:
        await client.cancel_run(uuid.uuid4())

    assert seen == ["Bearer neorc_secret"]


async def test_plain_http_beyond_loopback_is_allowed_when_asked_for() -> None:
    seen: list[str | None] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(204)

    async with HttpManagerClient(
        "manager.internal:8420",
        token="neorc_secret",
        allow_insecure=True,
        transport=httpx.MockTransport(record),
    ) as client:
        await client.cancel_run(uuid.uuid4())

    assert seen == ["Bearer neorc_secret"]


async def test_every_post_is_sent_as_json_body_or_not() -> None:
    seen: list[tuple[str, str | None]] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.headers.get("content-type")))
        return httpx.Response(204)

    transport = httpx.MockTransport(record)
    async with (
        HttpManagerClient("manager.test", transport=transport) as manager_client,
        HttpQueueClient("manager.test", transport=transport) as queue_client,
    ):
        await manager_client.cancel_run(uuid.uuid4())
        await queue_client.claim_task(uuid.uuid4())
        await queue_client.receive_task("default", timeout=0)

    assert seen == [("POST", "application/json")] * 3
