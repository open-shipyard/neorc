# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""A task's identity and lifecycle, the same in every store."""

from __future__ import annotations

import uuid
from enum import StrEnum

from neorc_core._errors import TaskStateError

TaskId = uuid.UUID


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
