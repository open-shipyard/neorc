# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Waking waiting workers and schedulers with LISTEN/NOTIFY.

One connection per manager process carries the ``LISTEN``; waiters hold nothing
but an in-process event. Idle workers therefore cost no connections, however
many of them are waiting. A second connection sends the announcements, because
the listening one is busy reading.

An announcement is a hint: a waiter that is woken re-reads the store, and one
that is not woken falls back on its poll timeout. So a request never waits for
one to be sent, and neither connection may turn a lost link into a manager
that needs restarting. ``notify`` only marks that there is something to
announce; one background task sends a single ``NOTIFY`` for every batch of
marks, on a connection it reopens when it breaks. Both connections are opened
with keepalives and a TCP user timeout, so a link dropped without a word is
found dead within a bound rather than at the kernel's retransmission limit.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from types import TracebackType
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from neorc_core import NeorcError, Subscription, TaskNotifier
from neorc_core._subscriptions import LocalSubscriptions

TASKS_CHANNEL = "neorc_task_ready"
"""The channel a published task is announced on, to wake waiting workers."""

EVENTS_CHANNEL = "neorc_event_ready"
"""The channel a new event is announced on, to wake a waiting scheduler."""

CHANNEL = TASKS_CHANNEL

RECONNECT_SECONDS = 1.0
"""How long to wait before reopening a connection that broke."""

CONNECT_TIMEOUT_SECONDS = 5
"""The most opening a connection may take."""

_LINK_DEFAULTS: dict[str, Any] = {
    "connect_timeout": CONNECT_TIMEOUT_SECONDS,
    "keepalives": 1,
    "keepalives_idle": 10,
    "keepalives_interval": 5,
    "keepalives_count": 3,
    "tcp_user_timeout": 30_000,
}
"""How a link that stopped answering is found dead, unless the DSN says otherwise.

Ignored by libpq where they do not apply, as on a Unix socket.
"""

_log = logging.getLogger(__name__)


class PostgresTaskNotifier(TaskNotifier):
    """Announces published tasks, or events, over a Postgres notification channel.

    Every manager process listening on the channel hears every announcement, so
    a worker waiting on one process is woken by a task published through
    another.
    """

    def __init__(self, dsn: str, *, channel: str = CHANNEL) -> None:
        self._dsn = dsn
        self._channel = channel
        self._pending = asyncio.Event()
        self._listening: asyncio.Task[None] | None = None
        self._announcing: asyncio.Task[None] | None = None
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
        """Open the connections and begin listening and announcing.

        Fails if the database cannot be reached now, so a misconfigured
        deployment does not start; a connection lost later is reopened.
        """
        if self._listening is not None:
            return
        listener = await self._listen()
        try:
            sender = await self._connect()
        except BaseException:
            await listener.close()
            raise
        self._listening = asyncio.create_task(self._dispatch(listener))
        self._announcing = asyncio.create_task(self._announce(sender))

    async def aclose(self) -> None:
        """Stop listening and announcing, and release the connections."""
        for task in (self._listening, self._announcing):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        self._listening = None
        self._announcing = None

    async def notify(self) -> None:
        """Mark that there is something to announce. Returns at once.

        The announcement follows from the background task; several marks
        before it gets to them are one announcement, which wakes every waiter
        all the same.
        """
        if self._announcing is None:
            raise NeorcError("the notifier is not started")
        self._pending.set()

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[Subscription]:
        async with self._subscriptions.subscribe() as subscription:
            yield subscription

    async def _connect(self) -> psycopg.AsyncConnection[Any]:
        given = conninfo_to_dict(self._dsn)
        defaults = {k: v for k, v in _LINK_DEFAULTS.items() if k not in given}
        return await psycopg.AsyncConnection.connect(
            make_conninfo(self._dsn, **defaults), autocommit=True
        )

    async def _listen(self) -> psycopg.AsyncConnection[Any]:
        connection = await self._connect()
        try:
            await connection.execute(f'LISTEN "{self._channel}"')
        except BaseException:
            await connection.close()
            raise
        return connection

    async def _announce(self, sender: psycopg.AsyncConnection[Any] | None) -> None:
        """Send one announcement per batch of marks, for as long as the notifier runs.

        A sending connection that breaks is dropped and reopened for the next
        announcement; one that cannot be reopened costs that batch, whose
        waiters fall back on their poll timeout.
        """
        try:
            while True:
                await self._pending.wait()
                self._pending.clear()
                sender = await self._send(sender)
        finally:
            if sender is not None:
                with suppress(Exception):
                    await sender.close()

    async def _send(
        self, sender: psycopg.AsyncConnection[Any] | None
    ) -> psycopg.AsyncConnection[Any] | None:
        """One announcement, on ``sender`` or a connection opened in its place.

        Returns the connection to send the next one on: ``None`` after a
        failure, so the next attempt reconnects.
        """
        for attempt in (1, 2):
            try:
                if sender is None or sender.closed:
                    sender = await self._connect()
                await sender.execute("SELECT pg_notify(%s, '')", (self._channel,))
                return sender
            except (psycopg.Error, OSError):
                if sender is not None:
                    with suppress(Exception):
                        await sender.close()
                sender = None
                if attempt == 1:
                    _log.warning("announcing on %r failed; reconnecting", self._channel)
                else:
                    _log.warning(
                        "announcement on %r lost; waiters fall back on their "
                        "poll timeout",
                        self._channel,
                        exc_info=True,
                    )
        return None

    async def _dispatch(self, listener: psycopg.AsyncConnection[Any]) -> None:
        """Wake local waiters for as long as the notifier runs.

        A listening connection that breaks is reopened after a pause, and every
        waiter is woken once it is, since announcements made meanwhile were
        missed: they re-read the store and find whatever arrived.
        """
        while True:
            try:
                async with listener:
                    async for _ in listener.notifies():
                        self._subscriptions.wake_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.warning(
                    "the listener on %r stopped; reconnecting in %ss",
                    self._channel,
                    RECONNECT_SECONDS,
                    exc_info=True,
                )
            while True:
                await asyncio.sleep(RECONNECT_SECONDS)
                try:
                    listener = await self._listen()
                    break
                except (psycopg.Error, OSError):
                    _log.warning("could not reopen the listener on %r", self._channel)
            self._subscriptions.wake_all()
