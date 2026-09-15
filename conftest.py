# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Fixtures shared by every package's tests.

They live at the root because pytest and mypy see the whole repository as one
run, and two directories cannot both hold a module called ``conftest``.

The Postgres fixtures run against a real server. Point
``NEORC_TEST_DATABASE_URL`` at one to use your own; otherwise a private server
is started for the session with ``pgserver``, so a plain ``uv sync && uv run
pytest`` needs no Docker and no system Postgres. Where neither is possible,
those tests skip.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from neorc_core import Manager, ManagerClient
from neorc_core.local import MemoryStore, MemoryTaskNotifier

if TYPE_CHECKING:
    from neorc.postgres import (
        PostgresCredentialStore,
        PostgresStore,
        PostgresTaskNotifier,
    )

# Before a test module imports the contract suites, so their asserts explain a
# failure the way asserts in test modules do.
pytest.register_assert_rewrite("neorc_core.testing.contracts")
pytest.register_assert_rewrite("neorc_core.testing.examples")

DATABASE_URL_ENV = "NEORC_TEST_DATABASE_URL"


@pytest.fixture
def manager() -> Manager:
    """A manager on the memory store, holding nothing."""
    return Manager(
        MemoryStore(), tasks=MemoryTaskNotifier(), events=MemoryTaskNotifier()
    )


@pytest.fixture(scope="session")
def database_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A Postgres to test against, from the environment or started for us."""
    configured = os.environ.get(DATABASE_URL_ENV)
    if configured:
        yield configured
        return

    pgserver = pytest.importorskip(
        "pgserver", reason=f"no Postgres: set {DATABASE_URL_ENV} or install pgserver"
    )
    data_dir: Path = tmp_path_factory.mktemp("pgdata")
    server = pgserver.get_server(str(data_dir), cleanup_mode="stop")
    try:
        yield str(server.get_uri())
    finally:
        server.cleanup()


@pytest.fixture
async def pg_schema(database_url: str) -> str:
    """The database URL, its neorc tables created and emptied."""
    import psycopg

    from neorc.postgres import create_schema
    from neorc.postgres._schema import TABLES

    await create_schema(database_url)
    async with await psycopg.AsyncConnection.connect(
        database_url, autocommit=True
    ) as conn:
        await conn.execute(f"TRUNCATE {', '.join(TABLES)}")
    return database_url


@pytest.fixture
async def pg_store(pg_schema: str) -> AsyncIterator[PostgresStore]:
    """An open store for flows on empty flow tables."""
    from neorc.postgres import PostgresStore

    async with PostgresStore(pg_schema) as store:
        yield store


@pytest.fixture
async def pg_credentials(pg_schema: str) -> AsyncIterator[PostgresCredentialStore]:
    """An open credential store on empty credential tables."""
    from neorc.postgres import PostgresCredentialStore

    async with PostgresCredentialStore(pg_schema) as credentials:
        yield credentials


@pytest.fixture
async def pg_notifier(database_url: str) -> AsyncIterator[PostgresTaskNotifier]:
    from neorc.postgres import PostgresTaskNotifier

    async with PostgresTaskNotifier(database_url) as notifier:
        yield notifier


# A manager on a real socket, and the examples deployed against it.

EXAMPLES = Path(__file__).parent / "examples"


def free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@asynccontextmanager
async def serve_app(app: Any) -> AsyncIterator[str]:
    """Serve an ASGI app on uvicorn at a free port, for the block; its address."""
    import uvicorn

    port = free_port()
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


@pytest.fixture
def own_tasks_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each example has a ``tasks`` module: import this one's, not another's."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "tasks", raising=False)
    yield
    sys.modules.pop("tasks", None)


@asynccontextmanager
async def deployed_example(
    manager_address: str, example: str, queues: list[str], *, scheduler: bool = True
) -> AsyncIterator[ManagerClient]:
    """A scheduler and a worker per queue on the HTTP clients, for the block.

    The client yielded reaches the same manager, to upload and start runs.
    Without ``scheduler``, only the workers: for more workers beside a block
    that already runs the deployment's one scheduler.
    """
    from neorc.http import HttpManagerClient, HttpQueueClient
    from neorc_core import Scheduler, Worker

    async with contextlib.AsyncExitStack() as stack:
        client = await stack.enter_async_context(
            HttpManagerClient(manager_address, poll_timeout=2)
        )
        schedulers = []
        if scheduler:
            schedulers.append(
                Scheduler(
                    await stack.enter_async_context(
                        HttpManagerClient(manager_address, poll_timeout=2)
                    ),
                    poll_timeout=2,
                )
            )
        workers = [
            Worker(
                await stack.enter_async_context(
                    HttpQueueClient(manager_address, poll_timeout=2)
                ),
                queue=queue,
                code_location=EXAMPLES / example,
                poll_timeout=2,
                lease_seconds=5,
            )
            for queue in queues
        ]
        running = [asyncio.create_task(s.run()) for s in schedulers]
        running += [asyncio.create_task(worker.run()) for worker in workers]
        try:
            yield client
        finally:
            for s in schedulers:
                s.stop()
            for worker in workers:
                worker.stop()
            await asyncio.wait_for(
                asyncio.gather(*running, return_exceptions=True), timeout=30
            )
