# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Assembling and running the manager process."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import uvicorn
from fastapi import FastAPI

from neorc.manager._app import DEFAULT_LONG_POLL_TIMEOUT, create_app
from neorc_core import Access, Manager

# A manager serves workers on other hosts, so it binds every interface.
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8420

DATABASE_URL_ENV = "NEORC_DATABASE_URL"
"""Where the reference deployment keeps its flows, runs, tasks and events."""


def build_app(
    database_url: str | None = None,
    *,
    auth: bool,
    create_schema: bool = False,
    long_poll_timeout: float = DEFAULT_LONG_POLL_TIMEOUT,
    ui: bool = True,
) -> FastAPI:
    """Build the application from the environment: store, notifiers, then routes.

    Which adapter backs the store is a deployment choice. This one is the
    reference: Postgres, from ``NEORC_DATABASE_URL``. Nothing above this
    function names it.

    Two notification channels: one wakes workers when a task is published,
    the other wakes a scheduler when an event is recorded. Both are hints sent
    after the transaction commits; a waiter re-reads the store.

    With ``auth``, every request to the API needs an API token, checked
    against a credential store on the same database, which the lifespan opens
    beside the flow store, after the schema step, and closes with it; without
    ``create_schema``, the lifespan first checks the credential tables exist,
    and fails with ``RuntimeError`` naming the fix if not. Without ``auth``,
    anyone who reaches the service may do everything.

    With ``ui``, the web UI from the ``neorc-ui`` distribution is served at
    ``/ui/``; ``FileNotFoundError`` names the fix if that holds no built UI,
    as in a checkout that was never built.
    """
    from neorc.postgres import (
        PostgresCredentialStore,
        PostgresStore,
        PostgresTaskNotifier,
    )
    from neorc.postgres import create_schema as create_postgres_schema
    from neorc.postgres._notifier import EVENTS_CHANNEL, TASKS_CHANNEL
    from neorc.postgres._schema import ensure_credential_tables

    dsn = database_url or os.environ.get(DATABASE_URL_ENV)
    if not dsn:
        raise RuntimeError(f"no database to serve: pass one or set {DATABASE_URL_ENV}")
    static = None
    if ui:
        import neorc_ui

        static = neorc_ui.static_dir()

    store = PostgresStore(dsn)
    tasks = PostgresTaskNotifier(dsn, channel=TASKS_CHANNEL)
    events = PostgresTaskNotifier(dsn, channel=EVENTS_CHANNEL)
    credentials = PostgresCredentialStore(dsn) if auth else None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if create_schema:
            await create_postgres_schema(dsn)
        elif credentials is not None:
            await ensure_credential_tables(dsn)
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(store)
            await stack.enter_async_context(tasks)
            await stack.enter_async_context(events)
            if credentials is not None:
                await stack.enter_async_context(credentials)
            yield

    app = create_app(
        Manager(store, tasks=tasks, events=events),
        access=Access(credentials) if credentials is not None else None,
        long_poll_timeout=long_poll_timeout,
        ui=static,
    )
    app.router.lifespan_context = lifespan
    return app


def run(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    auth: bool,
    database_url: str | None = None,
    create_schema: bool = False,
    ui: bool = True,
    ssl_certfile: str | None = None,
    ssl_keyfile: str | None = None,
) -> None:
    """Serve the manager until interrupted. Blocks.

    With ``ssl_certfile`` and ``ssl_keyfile``, it serves HTTPS. Raises
    ``RuntimeError`` before serving when there is no database, or when
    authentication is on and the credential tables are missing.
    """
    app = build_app(database_url, auth=auth, create_schema=create_schema, ui=ui)
    if auth and not create_schema:
        # Checked again by the lifespan; here, so the command can say so and
        # exit rather than leave it to uvicorn's startup failure.
        from neorc.postgres._schema import ensure_credential_tables

        asyncio.run(
            ensure_credential_tables(database_url or os.environ[DATABASE_URL_ENV])
        )
    uvicorn.run(
        app,
        host=host,
        port=port,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
    )
