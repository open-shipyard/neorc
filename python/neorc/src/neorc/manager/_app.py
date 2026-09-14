# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The manager's HTTP surface: the flow routes, on ``FlowManager``.

Each route is a thin translation between JSON and one manager call. The routes
that fetch a task and that report it started are separate on purpose: fetching
may move to another backend later, while reporting stays with the store.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from neorc._errors import error_body, status_of
from neorc.manager._flow_routes import router as flow_router
from neorc_core import FlowManager, InvalidValueError, NeorcError

DEFAULT_LONG_POLL_TIMEOUT = 25.0
"""How long a fetch waits before answering "nothing yet".

Must stay under the idle timeout of any proxy in front of the service; a worker
that hits it simply asks again.
"""


def create_app(
    flows: FlowManager, *, long_poll_timeout: float = DEFAULT_LONG_POLL_TIMEOUT
) -> FastAPI:
    """Build the ASGI application serving ``flows``."""
    app = FastAPI(title="neorc manager", version="0")
    app.state.flows = flows
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

    app.include_router(flow_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness, for load balancers and deployment scripts."""
        return {"status": "ok"}

    return app
