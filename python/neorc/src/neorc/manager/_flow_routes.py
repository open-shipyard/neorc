# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The manager's HTTP surface for flows: a translation onto ``FlowManager``.

Bodies and responses are the ``neorc_core._wire`` forms, so the direct and
HTTP clients send the same JSON. A request body is read as bytes, capped, and
checked for nesting depth before it is parsed, and only then handed to a model:
FastAPI's own parsing would hand a deeply nested body to the C parser first.
The models check the shape of the envelope alone; values inside it are checked
by core, so a request the in-memory manager accepts is not refused by HTTP
first, and one it refuses is refused the same way.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from neorc_core import FlowManager, InvalidValueError, PayloadTooLargeError, RunId
from neorc_core import _wire as wire
from neorc_core._values import ensure_json_depth
from neorc_core.flows import Version

MAX_BODY_BYTES = 16 * 1024 * 1024
"""The largest request body, well above the payload limit core enforces.

Only to protect the process: the size rules stay in core, where they apply to
what the in-memory manager receives too.
"""


def _get_flows(request: Request) -> FlowManager:
    """The flow manager this application was built around."""
    flows: FlowManager = request.app.state.flows
    return flows


Flows = Annotated[FlowManager, Depends(_get_flows)]
"""The flow manager, injected into a route; a module-level name, for FastAPI."""

_M = TypeVar("_M", bound=BaseModel)


async def read_body(request: Request, model: type[_M]) -> _M:
    """The request body as ``model``, read and checked before anything parses it.

    Raises ``PayloadTooLargeError`` past ``MAX_BODY_BYTES``, and
    ``InvalidValueError`` for a body that is not UTF-8, not JSON, nests too
    deep, or is not the shape ``model`` describes.
    """
    return _validate(await read_json(request), model)


async def read_json(request: Request) -> Any:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise PayloadTooLargeError(f"request body is over {MAX_BODY_BYTES} bytes")
        chunks.append(chunk)
    try:
        text = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidValueError(f"request body is not valid UTF-8: {exc}") from None
    ensure_json_depth(text)
    try:
        return json.loads(
            text, object_pairs_hook=_unique_keys, parse_constant=_not_a_number
        )
    except ValueError as exc:
        raise InvalidValueError(f"request body is not valid JSON: {exc}") from None


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    # json.loads would keep the last of two values under one key and drop the
    # rest without a word; what is stored must be what was sent.
    mapping: dict[str, Any] = {}
    for key, value in pairs:
        if key in mapping:
            raise InvalidValueError(
                f"request body: {key!r} appears more than once in the same object"
            )
        mapping[key] = value
    return mapping


def _not_a_number(token: str) -> Any:
    # json.loads takes NaN and Infinity, which JSON text cannot carry back.
    raise InvalidValueError(f"request body: {token} is not a JSON number")


def _validate(data: Any, model: type[_M]) -> _M:
    try:
        return model.model_validate(data, strict=True)
    except ValidationError as exc:
        raise InvalidValueError(f"request body: {exc}") from None


class UploadRequest(BaseModel):
    flows: list[Any]


class StartRunRequest(BaseModel):
    inputs: dict[str, Any]


router = APIRouter()


def _flow_response(flow: Any) -> dict[str, Any]:
    return {"name": flow.name, "version": str(flow.version), "content": flow.content}


@router.post("/flows")
async def upload_flows(request: Request, flows: Flows) -> dict[str, list[bool]]:
    """Upload flows deployed together; per flow, whether a version was stored."""
    body = await read_body(request, UploadRequest)
    return {"stored": await flows.upload_flows(body.flows)}


@router.get("/flows/{name}")
async def get_latest_flow(flows: Flows, name: str) -> dict[str, Any]:
    """A flow's latest version, with its content as uploaded."""
    return _flow_response(await flows.get_flow(name))


@router.get("/flows/{name}/versions/{version}")
async def get_flow_version(flows: Flows, name: str, version: str) -> dict[str, Any]:
    """One version of a flow."""
    try:
        parsed = Version.parse(version)
    except ValueError as exc:
        raise InvalidValueError(str(exc)) from None
    return _flow_response(await flows.get_flow(name, parsed))


@router.post("/flows/{name}/runs", status_code=status.HTTP_201_CREATED)
async def start_run(request: Request, flows: Flows, name: str) -> JSONResponse:
    """Start a run of the flow's latest version."""
    body = await read_body(request, StartRunRequest)
    run = await flows.start_run(name, body.inputs)
    return JSONResponse(wire.run_to(run), status_code=status.HTTP_201_CREATED)


@router.get("/runs/{run_id}")
async def get_run(flows: Flows, run_id: RunId) -> dict[str, Any]:
    """A run, for a status query."""
    return wire.run_to(await flows.get_run(run_id))


@router.post("/runs/{run_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_run(flows: Flows, run_id: RunId) -> Response:
    """Cancel a run by hand, and with it every active run in its tree."""
    await flows.cancel_run(run_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/runs/{run_id}/state")
async def run_state(flows: Flows, run_id: RunId) -> dict[str, Any]:
    """What a run's tasks and sub-flow runs have produced so far."""
    return wire.run_state_to(await flows.run_state(run_id))
