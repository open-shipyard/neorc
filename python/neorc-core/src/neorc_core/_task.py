# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The unit of work that travels from a publisher through the manager to a worker."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from neorc_core._errors import TaskStateError

TaskId = uuid.UUID

Payload = Mapping[str, Any]


class TaskStatus(StrEnum):
    """Where a task is in its lifecycle.

    A task is published ``PENDING``, becomes ``CLAIMED`` when a worker leases
    it, ``RUNNING`` once that worker reports it started, and ends ``SUCCEEDED``
    or ``FAILED``. A lease that lapses takes a ``CLAIMED`` or ``RUNNING`` task
    back to claimable without passing through a terminal status.
    """

    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


TERMINAL_STATUSES = frozenset({TaskStatus.SUCCEEDED, TaskStatus.FAILED})
"""Statuses a task never leaves."""

LEASED_STATUSES = frozenset({TaskStatus.CLAIMED, TaskStatus.RUNNING})
"""Statuses in which a worker holds the task and must keep heartbeating."""

_ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.CLAIMED}),
    TaskStatus.CLAIMED: frozenset(
        {TaskStatus.RUNNING, TaskStatus.SUCCEEDED, TaskStatus.FAILED}
    ),
    # Re-reporting a start is allowed: delivery is at-least-once, so a worker
    # that retried the call must not be told off for it.
    TaskStatus.RUNNING: frozenset(
        {TaskStatus.RUNNING, TaskStatus.SUCCEEDED, TaskStatus.FAILED}
    ),
    TaskStatus.SUCCEEDED: frozenset(),
    TaskStatus.FAILED: frozenset(),
}


def ensure_transition(current: TaskStatus, target: TaskStatus) -> None:
    """Raise ``TaskStateError`` unless ``current`` may become ``target``.

    Every store enforces the same rules, so an adapter cannot invent its own
    lifecycle.
    """
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise TaskStateError(
            f"a {current.value} task cannot become {target.value}",
        )


@dataclass(frozen=True, slots=True)
class Task:
    """A published task, as the manager records it."""

    id: TaskId
    name: str
    payload: Payload
    status: TaskStatus
    created_at: datetime
    run_after: datetime
    priority: int = 0
    attempts: int = 0
    """How many times this task has been handed to a worker, this one included.

    Delivery is at-least-once, so a handler can use this to tell a retry from a
    first run.
    """

    lease_expires_at: datetime | None = None
    """When the current claim lapses and the task becomes claimable again.

    ``None`` while the task is not claimed.
    """

    error: str | None = None
    """Why the task failed, on a ``FAILED`` task. ``None`` otherwise."""
