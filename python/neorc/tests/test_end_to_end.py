# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The whole thing: a manager on a socket, Postgres underneath, workers over HTTP.

Nothing here is stubbed. The example scenarios of ``neorc_core.testing.examples``
run here as they run in memory, with a scheduler and one worker per queue on
the HTTP clients: if this passes, the pieces fit.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn

from neorc.http import HttpManagerClient, HttpQueueClient
from neorc.manager import build_app
from neorc_core import Scheduler, Worker
from neorc_core.testing import examples

pytestmark = pytest.mark.postgres

EXAMPLES = Path(__file__).parents[3] / "examples"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@pytest.fixture
async def manager_address(
    pg_schema: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[str]:
    """A manager service listening on a real port, backed by a real database.

    It serves a stand-in UI: the built one needs Node.js, which Python tests
    never do.
    """
    (tmp_path / "index.html").write_text("<!doctype html><title>stand-in</title>")
    monkeypatch.setattr("neorc_ui.STATIC", tmp_path)
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


async def test_the_manager_serves_the_ui_and_the_listings(
    manager_address: str,
) -> None:
    async with httpx.AsyncClient(base_url=manager_address) as http:
        page = await http.get("/ui/")
        flows = await http.get("/flows")
        runs = await http.get("/runs")

    assert page.status_code == 200 and "stand-in" in page.text
    assert "content-security-policy" in page.headers
    assert flows.status_code == 200 and flows.json() == {"flows": []}
    assert runs.status_code == 200 and runs.json() == {"runs": []}
