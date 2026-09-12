# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The port that wakes waiting workers when work arrives."""

from __future__ import annotations

from abc import ABC, abstractmethod


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
    async def wait(self, *, timeout: float) -> bool:
        """Wait for an announcement; return ``False`` if ``timeout`` elapsed first."""
        raise NotImplementedError
