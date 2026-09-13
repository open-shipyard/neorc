# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Assembling and running the manager process."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from neorc.manager._app import DEFAULT_LONG_POLL_TIMEOUT, create_app
from neorc_core import Manager

# A manager serves workers on other hosts, so it binds every interface.
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8420

DATABASE_URL_ENV = "NEORC_DATABASE_URL"
"""Where the reference deployment keeps its tasks."""


def build_app(
    database_url: str | None = None,
    *,
    create_schema: bool = False,
    long_poll_timeout: float = DEFAULT_LONG_POLL_TIMEOUT,
) -> FastAPI:
    """Build the application from the environment: store, notifier, then routes.

    Which adapter backs the store is a deployment choice. This one is the
    reference: Postgres, from ``NEORC_DATABASE_URL``. Nothing above this
    function names it.
    """
    from neorc.postgres import PostgresTaskNotifier, PostgresTaskStore
    from neorc.postgres import create_schema as create_postgres_schema

    dsn = database_url or os.environ.get(DATABASE_URL_ENV)
    if not dsn:
        raise RuntimeError(f"no database to serve: pass one or set {DATABASE_URL_ENV}")

    store = PostgresTaskStore(dsn)
    notifier = PostgresTaskNotifier(dsn)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if create_schema:
            await create_postgres_schema(dsn)
        await store.open()
        await notifier.start()
        try:
            yield
        finally:
            await notifier.aclose()
            await store.aclose()

    app = create_app(
        Manager(store, notifier),
        long_poll_timeout=long_poll_timeout,
    )
    app.router.lifespan_context = lifespan
    return app


def run(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    database_url: str | None = None,
    create_schema: bool = False,
) -> None:
    """Serve the manager until interrupted. Blocks."""
    app = build_app(database_url, create_schema=create_schema)
    uvicorn.run(app, host=host, port=port)
