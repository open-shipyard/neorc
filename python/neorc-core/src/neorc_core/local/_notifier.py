# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The task notifier for a single process."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from neorc_core._subscriptions import LocalSubscriptions
from neorc_core.ports._task_notifier import Subscription, TaskNotifier


class MemoryTaskNotifier(TaskNotifier):
    """Wakes waiters in this process only."""

    def __init__(self) -> None:
        self._subscriptions = LocalSubscriptions()

    async def notify(self) -> None:
        self._subscriptions.wake_all()

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[Subscription]:
        async with self._subscriptions.subscribe() as subscription:
            yield subscription
