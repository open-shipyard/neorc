# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The manager's HTTP surface.

Each route is a thin translation between JSON and ``neorc_core.Manager``. The
routes that fetch a task and that report it started are separate on purpose:
fetching may move to another backend later, while reporting stays with the store.
"""

from __future__ import annotations

from fastapi import FastAPI

from neorc.manager._schemas import (
    FinishedRequest,
    HeartbeatRequest,
    LeaseResponse,
    PublishRequest,
    StatusResponse,
    TaskResponse,
)
from neorc_core import Manager, TaskId

DEFAULT_LONG_POLL_TIMEOUT = 25.0


def create_app(
    manager: Manager, *, long_poll_timeout: float = DEFAULT_LONG_POLL_TIMEOUT
) -> FastAPI:
    """Build the ASGI application serving ``manager``.

    ``long_poll_timeout`` must stay under the idle timeout of any proxy in
    front of the service; a worker that hits it simply polls again.
    """
    raise NotImplementedError


async def publish_task(manager: Manager, request: PublishRequest) -> TaskResponse:
    """POST /tasks — enqueue a task."""
    raise NotImplementedError


async def pick_next_task(manager: Manager, *, timeout: float) -> TaskResponse | None:
    """POST /tasks/next — long-poll for a task to run. 204 when nothing arrives."""
    raise NotImplementedError


async def extend_lease(
    manager: Manager, task_id: TaskId, request: HeartbeatRequest
) -> LeaseResponse:
    """POST /tasks/{task_id}/heartbeat — a worker keeping the task it holds."""
    raise NotImplementedError


async def report_started(manager: Manager, task_id: TaskId) -> None:
    """POST /tasks/{task_id}/started — a worker began executing a claimed task."""
    raise NotImplementedError


async def report_finished(
    manager: Manager, task_id: TaskId, request: FinishedRequest
) -> None:
    """POST /tasks/{task_id}/finished — a worker finished, well or badly."""
    raise NotImplementedError


async def get_task_status(manager: Manager, task_id: TaskId) -> StatusResponse:
    """GET /tasks/{task_id} — where a task has got to."""
    raise NotImplementedError
