# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The JSON bodies the manager accepts and returns."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from neorc_core import Payload, TaskId, TaskStatus


class PublishRequest(BaseModel):
    """A publisher enqueueing a task."""

    name: str
    payload: Payload
    run_after: datetime | None = None
    priority: int = 0


class TaskResponse(BaseModel):
    """A task as the API renders it."""

    id: TaskId
    name: str
    payload: Payload
    status: TaskStatus
    created_at: datetime
    run_after: datetime
    priority: int
    attempts: int
    lease_expires_at: datetime | None


class HeartbeatRequest(BaseModel):
    """A worker asking for more time on a task it holds."""

    lease_seconds: float | None = None


class LeaseResponse(BaseModel):
    """When the lease a worker holds now lapses."""

    id: TaskId
    lease_expires_at: datetime


class StatusResponse(BaseModel):
    """The answer to a status query."""

    id: TaskId
    status: TaskStatus


class FinishedRequest(BaseModel):
    """A worker reporting how a task ended."""

    error: str | None = None
