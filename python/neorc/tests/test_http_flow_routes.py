# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The flow routes over an ASGI transport, on the memory store.

Routing, status codes and request validation only: what a flow means is
settled in core, and the HTTP clients are held to the client contracts.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from neorc.manager import create_app
from neorc.manager._flow_routes import MAX_BODY_BYTES
from neorc_core import FlowManager, Manager
from neorc_core._values import MAX_JSON_DEPTH, JsonValue
from neorc_core.local import MemoryStore, MemoryTaskNotifier

MAIN: JsonValue = {
    "name": "main",
    "version": "1.0.0",
    "inputs": {"word": "string"},
    "output": "tasks.work",
    "steps": {"work": {"handler": "tasks:work", "params": {"word": "inputs.word"}}},
}


@pytest.fixture
def flows() -> FlowManager:
    return FlowManager(
        MemoryStore(), tasks=MemoryTaskNotifier(), events=MemoryTaskNotifier()
    )


@pytest.fixture
async def http(
    manager: Manager, flows: FlowManager
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(manager, flows=flows, long_poll_timeout=2)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://manager.test"
    ) as client:
        yield client


async def _started(http: httpx.AsyncClient) -> str:
    await http.post("/flows", json={"flows": [MAIN]})
    response = await http.post("/flows/main/runs", json={"inputs": {"word": "hi"}})
    assert response.status_code == 201
    run_id: str = response.json()["id"]
    return run_id


def _error(response: httpx.Response) -> str:
    error: str = response.json()["error"]
    return error


async def test_flows_are_uploaded_under_the_version_rules(
    http: httpx.AsyncClient,
) -> None:
    assert isinstance(MAIN, dict)
    uploaded = await http.post("/flows", json={"flows": [MAIN]})
    again = await http.post("/flows", json={"flows": [MAIN]})
    changed = await http.post(
        "/flows", json={"flows": [{**MAIN, "inputs": {"word": "number"}}]}
    )
    invalid = await http.post("/flows", json={"flows": [{"name": "broken"}]})

    assert (uploaded.status_code, uploaded.json()) == (200, {"stored": [True]})
    assert again.json() == {"stored": [False]}
    assert (changed.status_code, _error(changed)) == (409, "FlowVersionError")
    assert (invalid.status_code, _error(invalid)) == (422, "FlowDefinitionError")
    assert any("missing field" in p for p in invalid.json()["problems"])


async def test_a_flow_reads_back_by_name_and_by_version(
    http: httpx.AsyncClient,
) -> None:
    await http.post("/flows", json={"flows": [MAIN]})

    latest = await http.get("/flows/main")
    version = await http.get("/flows/main/versions/1.0.0")
    missing = await http.get("/flows/main/versions/2.0.0")
    unknown = await http.get("/flows/nothing")
    malformed = await http.get("/flows/main/versions/latest")

    assert latest.status_code == 200
    assert latest.json() == {"name": "main", "version": "1.0.0", "content": MAIN}
    assert version.json() == latest.json()
    assert (missing.status_code, _error(missing)) == (404, "FlowNotFoundError")
    assert (unknown.status_code, _error(unknown)) == (404, "FlowNotFoundError")
    assert (malformed.status_code, _error(malformed)) == (422, "InvalidValueError")


async def test_a_run_starts_reads_back_and_is_cancelled(
    http: httpx.AsyncClient,
) -> None:
    run_id = await _started(http)

    run = await http.get(f"/runs/{run_id}")
    state = await http.get(f"/runs/{run_id}/state")
    cancelled = await http.post(f"/runs/{run_id}/cancel")
    again = await http.post(f"/runs/{run_id}/cancel")

    assert run.status_code == 200
    assert run.json()["flow"] == "main"
    assert run.json()["inputs"] == {"word": "hi"}
    assert run.json()["status"] == "active"
    assert state.json() == {"inputs": {"word": "hi"}, "steps": []}
    assert cancelled.status_code == 204
    assert (again.status_code, _error(again)) == (409, "RunStateError")
    assert (await http.get(f"/runs/{run_id}")).json()["status"] == "cancelled"


async def test_requests_the_manager_refuses_carry_the_error_they_hit(
    http: httpx.AsyncClient,
) -> None:
    await http.post("/flows", json={"flows": [MAIN]})
    missing = uuid.uuid4()

    bad_inputs = await http.post("/flows/main/runs", json={"inputs": {"word": 1}})
    no_flow = await http.post("/flows/nothing/runs", json={"inputs": {}})
    no_run = await http.get(f"/runs/{missing}")
    no_state = await http.get(f"/runs/{missing}/state")
    no_cancel = await http.post(f"/runs/{missing}/cancel")
    not_an_id = await http.get("/runs/not-a-uuid")
    nul_name = await http.get("/flows/main%00")

    assert (bad_inputs.status_code, _error(bad_inputs)) == (422, "InvalidValueError")
    assert (nul_name.status_code, _error(nul_name)) == (422, "InvalidValueError")
    assert "is not a string" in bad_inputs.json()["detail"]
    assert (no_flow.status_code, _error(no_flow)) == (404, "FlowNotFoundError")
    for response in (no_run, no_state, no_cancel):
        assert (response.status_code, _error(response)) == (404, "RunNotFoundError")
    assert (not_an_id.status_code, _error(not_an_id)) == (422, "InvalidValueError")


@pytest.mark.parametrize(
    ("content", "error"),
    [
        (
            b"[" * (MAX_JSON_DEPTH + 1) + b"]" * (MAX_JSON_DEPTH + 1),
            "InvalidValueError",
        ),
        (b"[" * 100_000, "InvalidValueError"),
        (b"{not json", "InvalidValueError"),
        (b'{"flows": "\xff"}', "InvalidValueError"),
        (b'{"flows": "not a list"}', "InvalidValueError"),
        (b'{"inputs": {}}', "InvalidValueError"),
        (b'{"flows": [], "flows": []}', "InvalidValueError"),
        (b'{"flows": [NaN]}', "InvalidValueError"),
        (b'{"flows": [-Infinity]}', "InvalidValueError"),
        (b'{"flows": [' + b" " * MAX_BODY_BYTES + b"]}", "PayloadTooLargeError"),
    ],
    ids=[
        "one-too-deep",
        "stack-deep",
        "not-json",
        "not-utf-8",
        "wrong-shape",
        "missing-field",
        "repeated-key",
        "nan",
        "infinity",
        "over-the-body-limit",
    ],
)
async def test_bodies_are_checked_before_anything_parses_them(
    http: httpx.AsyncClient, content: bytes, error: str
) -> None:
    response = await http.post(
        "/flows", content=content, headers={"content-type": "application/json"}
    )

    assert response.status_code == (413 if error == "PayloadTooLargeError" else 422)
    assert _error(response) == error


async def test_values_are_checked_by_core_not_by_the_envelope(
    http: httpx.AsyncClient,
) -> None:
    """A value of the wrong JSON type reaches core, which says what is wrong with it."""
    await http.post("/flows", json={"flows": [MAIN]})
    body: dict[str, Any] = {"inputs": {"word": [1, {"$datetime": "x"}]}}

    response = await http.post("/flows/main/runs", content=json.dumps(body).encode())

    assert (response.status_code, _error(response)) == (422, "InvalidValueError")
    assert "input 'word' is not a string" in response.json()["detail"]


async def test_the_task_api_answers_errors_in_the_same_form(
    http: httpx.AsyncClient,
) -> None:
    response = await http.get(f"/tasks/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"] == "TaskNotFoundError"
    assert "detail" in response.json()
