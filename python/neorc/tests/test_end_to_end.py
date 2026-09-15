# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The whole thing: a manager on a socket, Postgres underneath, workers over HTTP.

Nothing here is stubbed. The example scenarios of ``neorc_core.testing.examples``
run here as they run in memory, with a scheduler and one worker per queue on
the HTTP clients: if this passes, the pieces fit. The manager needs a token, as
a deployment's does, created as ``neorc tokens create`` creates one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from conftest import EXAMPLES, deployed_example, serve_app

from neorc.manager import build_app
from neorc_core import Access
from neorc_core.testing import examples

pytestmark = pytest.mark.postgres


@pytest.fixture
async def token(pg_schema: str) -> str:
    """A token the manager accepts."""
    from neorc.postgres import PostgresCredentialStore, create_schema

    await create_schema(pg_schema)
    async with PostgresCredentialStore(pg_schema) as credentials:
        secret, _ = await Access(credentials).create_token("end-to-end")
    return secret


@pytest.fixture
async def manager_address(
    pg_schema: str, token: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[str]:
    """A manager service listening on a real port, backed by a real database.

    It serves a stand-in UI: the built one needs Node.js, which Python tests
    never do.
    """
    (tmp_path / "index.html").write_text("<!doctype html><title>stand-in</title>")
    monkeypatch.setattr("neorc_ui.STATIC", tmp_path)
    app = build_app(pg_schema, auth=True, create_schema=True, long_poll_timeout=2)
    async with serve_app(app) as address:
        yield address


# The examples, deployed.


@pytest.mark.usefixtures("own_tasks_module")
@pytest.mark.parametrize("flow", ["a", "b"])
async def test_hello_runs_deployed(
    manager_address: str, token: str, flow: str, capfd: pytest.CaptureFixture[str]
) -> None:
    async with deployed_example(
        manager_address, "hello", ["default"], token=token
    ) as client:
        await examples.hello(client, EXAMPLES, flow)

    assert capfd.readouterr().out == f"{flow}\n"


@pytest.mark.usefixtures("own_tasks_module")
async def test_word_picker_runs_deployed(manager_address: str, token: str) -> None:
    async with deployed_example(
        manager_address, "wordplay", ["default", "scoring"], token=token
    ) as client:
        await examples.word_picker(client, EXAMPLES)


@pytest.mark.usefixtures("own_tasks_module")
async def test_word_picker_rounds_runs_deployed(
    manager_address: str, token: str
) -> None:
    async with deployed_example(
        manager_address, "wordplay", ["default", "scoring"], token=token
    ) as client:
        await examples.word_picker_rounds(client, EXAMPLES)


async def test_the_manager_serves_the_ui_and_the_listings(
    manager_address: str, token: str
) -> None:
    async with httpx.AsyncClient(base_url=manager_address) as http:
        page = await http.get("/ui/")
        anonymous = await http.get("/flows")
        headers = {"authorization": f"Bearer {token}"}
        flows = await http.get("/flows", headers=headers)
        runs = await http.get("/runs", headers=headers)

    assert page.status_code == 200 and "stand-in" in page.text
    assert "content-security-policy" in page.headers
    assert anonymous.status_code == 401
    assert flows.status_code == 200 and flows.json() == {"flows": []}
    assert runs.status_code == 200 and runs.json() == {"runs": []}
