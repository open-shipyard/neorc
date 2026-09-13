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

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from neorc_core import Manager
from neorc_core.testing import MemoryTaskNotifier, MemoryTaskStore

if TYPE_CHECKING:
    from neorc.postgres import PostgresTaskNotifier, PostgresTaskStore

DATABASE_URL_ENV = "NEORC_TEST_DATABASE_URL"


@pytest.fixture
def store() -> MemoryTaskStore:
    return MemoryTaskStore()


@pytest.fixture
def notifier() -> MemoryTaskNotifier:
    return MemoryTaskNotifier()


@pytest.fixture
def manager(store: MemoryTaskStore, notifier: MemoryTaskNotifier) -> Manager:
    return Manager(store, notifier)


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
async def pg_store(database_url: str) -> AsyncIterator[PostgresTaskStore]:
    """An open store on an empty tasks table."""
    from neorc.postgres import PostgresTaskStore, create_schema
    from neorc.postgres._schema import TASKS_TABLE

    await create_schema(database_url)
    async with PostgresTaskStore(database_url) as store:
        async with store.pool.connection() as conn:
            await conn.execute(f"TRUNCATE {TASKS_TABLE}")
        yield store


@pytest.fixture
async def pg_notifier(database_url: str) -> AsyncIterator[PostgresTaskNotifier]:
    from neorc.postgres import PostgresTaskNotifier

    async with PostgresTaskNotifier(database_url) as notifier:
        yield notifier
