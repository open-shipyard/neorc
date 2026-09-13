# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The port that wakes waiting workers when work arrives."""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager


class Subscription(ABC):
    """A caller's standing interest in being told that work arrived.

    Announcements that land between two ``wait`` calls are remembered, so a
    caller that claims, finds nothing and then waits never sleeps through a task
    published in that gap.
    """

    @abstractmethod
    async def wait(self, *, timeout: float) -> bool:
        """Wait for an announcement; return ``False`` if ``timeout`` elapsed first.

        Returns immediately if one arrived since the last call.
        """
        raise NotImplementedError


class TaskNotifier(ABC):
    """Announces that a task may be ready, so waiters stop waiting.

    A wakeup is a hint, not a promise: a woken waiter that finds nothing to
    claim waits again. Implementations must not hold a resource per waiter.
    """

    @abstractmethod
    async def notify(self) -> None:
        """Announce that a task was published."""
        raise NotImplementedError

    @abstractmethod
    def subscribe(self) -> AbstractAsyncContextManager[Subscription]:
        """Start listening for announcements for the duration of the context."""
        raise NotImplementedError
