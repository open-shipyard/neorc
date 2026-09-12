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

TaskId = uuid.UUID

Payload = Mapping[str, Any]


class TaskStatus(StrEnum):
    """Where a task is in its lifecycle.

    The set of statuses and the transitions between them are still open; see
    docs/specs/postgres-implementation.md.
    """

    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


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
