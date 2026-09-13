# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Waking waiting workers with LISTEN/NOTIFY.

One connection per manager process carries the ``LISTEN``; waiters hold nothing
but an in-process event. Idle workers therefore cost no connections, however
many of them are waiting. A second connection sends the announcements, because
the listening one is busy reading.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from types import TracebackType
from typing import Any

import psycopg

from neorc_core import NeorcError, Subscription, TaskNotifier
from neorc_core._subscriptions import LocalSubscriptions

CHANNEL = "neorc_task_ready"

_log = logging.getLogger(__name__)


class PostgresTaskNotifier(TaskNotifier):
    """Announces published tasks over a Postgres notification channel.

    Every manager process listening on the channel hears every announcement, so
    a worker waiting on one process is woken by a task published through
    another.
    """

    def __init__(self, dsn: str, *, channel: str = CHANNEL) -> None:
        self._dsn = dsn
        self._channel = channel
        self._connection: psycopg.AsyncConnection[Any] | None = None
        self._sender: psycopg.AsyncConnection[Any] | None = None
        self._listening: asyncio.Task[None] | None = None
        self._subscriptions = LocalSubscriptions()

    async def __aenter__(self) -> PostgresTaskNotifier:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def start(self) -> None:
        """Open the listening connection and begin dispatching notifications."""
        if self._listening is not None:
            return
        self._connection = await psycopg.AsyncConnection.connect(
            self._dsn, autocommit=True
        )
        self._sender = await psycopg.AsyncConnection.connect(self._dsn, autocommit=True)
        await self._connection.execute(f'LISTEN "{self._channel}"')
        self._listening = asyncio.create_task(self._dispatch())

    async def aclose(self) -> None:
        """Stop listening and release the connections."""
        if self._listening is not None:
            self._listening.cancel()
            with suppress(asyncio.CancelledError):
                await self._listening
            self._listening = None
        for connection in (self._connection, self._sender):
            if connection is not None:
                await connection.close()
        self._connection = None
        self._sender = None

    async def notify(self) -> None:
        if self._sender is None:
            raise NeorcError("the notifier is not started")
        await self._sender.execute("SELECT pg_notify(%s, '')", (self._channel,))

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[Subscription]:
        async with self._subscriptions.subscribe() as subscription:
            yield subscription

    async def _dispatch(self) -> None:
        """Wake local waiters for as long as the channel delivers."""
        if self._connection is None:  # unreachable: start() opens it first
            raise NeorcError("the notifier is not started")
        try:
            async for _ in self._connection.notifies():
                self._subscriptions.wake_all()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Losing the listener costs latency, not correctness: workers fall
            # back on their poll timeout until the manager is restarted.
            _log.exception("the task notification listener stopped")
