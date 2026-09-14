# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The manager's HTTP surface.

Each route is a thin translation between JSON and ``neorc_core.Manager``, or
``FlowManager`` for the flow routes. The routes that fetch a task and that
report it started are separate on purpose: fetching may move to another backend
later, while reporting stays with the store.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from neorc._errors import error_body, status_of
from neorc.manager._flow_routes import router as flow_router
from neorc.manager._schemas import (
    FinishedRequest,
    HeartbeatRequest,
    LeaseResponse,
    PublishRequest,
    TaskResponse,
)
from neorc_core import FlowManager, InvalidValueError, Manager, NeorcError, TaskId
from neorc_core.ports._queue_client import DEFAULT_LEASE_SECONDS


def _get_manager(request: Request) -> Manager:
    """The manager this application was built around."""
    manager: Manager = request.app.state.manager
    return manager


Managed = Annotated[Manager, Depends(_get_manager)]
"""The manager, injected into a route.

Defined at module level: annotations are strings here, so FastAPI has to be able
to resolve this name in the module's globals.
"""

DEFAULT_LONG_POLL_TIMEOUT = 25.0
"""How long a fetch waits before answering "nothing yet".

Must stay under the idle timeout of any proxy in front of the service; a worker
that hits it simply asks again.
"""


def create_app(
    manager: Manager,
    *,
    flows: FlowManager | None = None,
    long_poll_timeout: float = DEFAULT_LONG_POLL_TIMEOUT,
) -> FastAPI:
    """Build the ASGI application serving ``manager``, and ``flows`` if given."""
    app = FastAPI(title="neorc manager", version="0")
    app.state.manager = manager
    app.state.long_poll_timeout = long_poll_timeout

    @app.exception_handler(NeorcError)
    async def _neorc_error(request: Request, exc: NeorcError) -> JSONResponse:
        return JSONResponse(error_body(exc), status_code=status_of(exc))

    @app.exception_handler(RequestValidationError)
    async def _bad_request(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # A path or query parameter FastAPI could not read, in the same form
        # as every other refusal, so a client raises one kind of error for it.
        error = InvalidValueError(f"request: {exc}")
        return JSONResponse(error_body(error), status_code=status_of(error))

    if flows is not None:
        app.state.flows = flows
        app.include_router(flow_router)

    @app.post("/tasks", status_code=status.HTTP_201_CREATED)
    async def publish_task(manager: Managed, body: PublishRequest) -> TaskResponse:
        """Enqueue a task."""
        task = await manager.publish(
            body.name,
            body.payload,
            run_after=body.run_after,
            priority=body.priority,
        )
        return TaskResponse.of(task)

    @app.post("/tasks/next")
    async def pick_next_task(
        request: Request,
        manager: Managed,
        timeout: Annotated[float | None, Query(ge=0)] = None,
        lease_seconds: Annotated[float, Query(gt=0)] = DEFAULT_LEASE_SECONDS,
    ) -> Response:
        """Long-poll for a task to run. 204 when the wait ends empty."""
        limit: float = request.app.state.long_poll_timeout
        waited = limit if timeout is None else min(timeout, limit)
        task = await manager.pick_next_task(timeout=waited, lease_seconds=lease_seconds)
        if task is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        return JSONResponse(
            TaskResponse.of(task).model_dump(mode="json"),
            status_code=status.HTTP_200_OK,
        )

    @app.post("/tasks/{task_id}/started", status_code=status.HTTP_204_NO_CONTENT)
    async def report_started(manager: Managed, task_id: TaskId) -> None:
        """A worker began executing a task it holds."""
        await manager.report_started(task_id)

    @app.post("/tasks/{task_id}/heartbeat")
    async def extend_lease(
        manager: Managed, task_id: TaskId, body: HeartbeatRequest
    ) -> LeaseResponse:
        """A worker keeping the task it holds."""
        expires_at = await manager.extend_lease(
            task_id,
            lease_seconds=(
                body.lease_seconds
                if body.lease_seconds is not None
                else DEFAULT_LEASE_SECONDS
            ),
        )
        return LeaseResponse(id=task_id, lease_expires_at=expires_at)

    @app.post("/tasks/{task_id}/finished", status_code=status.HTTP_204_NO_CONTENT)
    async def report_finished(
        manager: Managed, task_id: TaskId, body: FinishedRequest
    ) -> None:
        """A worker finished, well or badly."""
        await manager.report_finished(task_id, error=body.error)

    @app.get("/tasks/{task_id}")
    async def get_task(manager: Managed, task_id: TaskId) -> TaskResponse:
        """Where a task has got to."""
        return TaskResponse.of(await manager.get_task(task_id))

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness, for load balancers and deployment scripts."""
        return {"status": "ok"}

    return app
