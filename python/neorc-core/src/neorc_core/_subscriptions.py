# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""In-process fan-out of announcements to waiting callers.

Every notifier needs the same thing: hand a wakeup to whoever is waiting right
now, without holding a connection, a thread or a task per waiter. All a waiter
costs here is an ``asyncio.Event``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from neorc_core.ports._task_notifier import Subscription


class _LocalSubscription(Subscription):
    """One waiter's event, plus a flag for announcements it has not consumed."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def wake(self) -> None:
        self._event.set()

    async def wait(self, *, timeout: float) -> bool:
        if timeout <= 0:
            return self._event.is_set()
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
        except TimeoutError:
            return False
        finally:
            # Consume whatever arrived, so the next wait blocks again.
            self._event.clear()
        return True


class LocalSubscriptions:
    """The set of subscriptions a notifier currently has to wake."""

    def __init__(self) -> None:
        self._subscriptions: set[_LocalSubscription] = set()

    def __len__(self) -> int:
        return len(self._subscriptions)

    def wake_all(self) -> None:
        """Wake every current waiter. Cheap, and safe to call with none."""
        for subscription in self._subscriptions:
            subscription.wake()

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[Subscription]:
        """Register a waiter for the duration of the context."""
        subscription = _LocalSubscription()
        self._subscriptions.add(subscription)
        try:
            yield subscription
        finally:
            self._subscriptions.discard(subscription)
