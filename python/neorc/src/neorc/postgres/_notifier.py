# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Waking waiting workers with LISTEN/NOTIFY.

One connection per manager process carries the ``LISTEN``; waiters hold nothing
but an in-process event. Idle workers therefore cost no connections, however
many of them are waiting.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

import psycopg

from neorc_core import TaskNotifier

CHANNEL = "neorc_task_ready"


class PostgresTaskNotifier(TaskNotifier):
    """Announces published tasks over a Postgres notification channel."""

    def __init__(self, dsn: str, *, channel: str = CHANNEL) -> None:
        self._dsn = dsn
        self._channel = channel
        self._connection: psycopg.AsyncConnection[Any] | None = None

    async def __aenter__(self) -> PostgresTaskNotifier:
        raise NotImplementedError

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        raise NotImplementedError

    async def start(self) -> None:
        """Open the listening connection and begin dispatching notifications."""
        raise NotImplementedError

    async def aclose(self) -> None:
        """Stop listening and release the connection."""
        raise NotImplementedError

    async def notify(self) -> None:
        raise NotImplementedError

    async def wait(self, *, timeout: float) -> bool:
        raise NotImplementedError
