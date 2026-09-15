# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The guard on the manager's routes: API tokens, and writes from other sites.

Over an ASGI transport, on the memory stores. What a token is, and when it is
refused, is settled in core; here, only what HTTP adds: where the token is
read from, the statuses, which routes are guarded, and before what.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from neorc.manager import create_app
from neorc_core import Access, Manager
from neorc_core.local import MemoryCredentialStore

FLOW = {"name": "f", "version": "1.0.0", "steps": {"work": {"handler": "m:work"}}}
JSON = {"content-type": "application/json"}


@pytest.fixture
def access() -> Access:
    return Access(MemoryCredentialStore())


@pytest.fixture
def static(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<!doctype html><title>ui</title>")
    return tmp_path


@pytest.fixture
async def guarded(
    manager: Manager, access: Access, static: Path
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(manager, access=access, long_poll_timeout=1, ui=static)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://manager.test"
    ) as client:
        yield client


@pytest.fixture
async def open_app(manager: Manager) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(manager, access=None, long_poll_timeout=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://manager.test"
    ) as client:
        yield client


def bearer(secret: str) -> dict[str, str]:
    return {"authorization": f"Bearer {secret}", **JSON}


def _error(response: httpx.Response) -> str:
    error: str = response.json()["error"]
    return error


# Tokens.


async def test_a_request_without_a_token_is_refused_and_told_how(
    guarded: httpx.AsyncClient,
) -> None:
    response = await guarded.get("/runs")

    assert (response.status_code, _error(response)) == (401, "AuthenticationError")
    assert response.headers["www-authenticate"] == "Bearer"
    assert "Authorization: Bearer" in response.json()["detail"]


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer neorc_not-a-token-this-manager-issued",
        "Bearer not-even-shaped-like-one",
        "Basic dXNlcjpwYXNz",
        "neorc_without-a-scheme",
    ],
)
async def test_a_request_with_a_token_not_accepted_is_refused(
    guarded: httpx.AsyncClient, access: Access, authorization: str
) -> None:
    await access.create_token("ci")

    response = await guarded.get("/runs", headers={"authorization": authorization})

    assert (response.status_code, _error(response)) == (401, "AuthenticationError")
    secret = authorization.partition(" ")[2]
    assert not secret or secret not in response.text


async def test_a_token_is_accepted_on_every_kind_of_route(
    guarded: httpx.AsyncClient, access: Access
) -> None:
    secret, _ = await access.create_token("ci")
    headers = bearer(secret)

    uploaded = await guarded.post("/flows", json={"flows": [FLOW]}, headers=headers)
    run = await guarded.post("/flows/f/runs", json={"inputs": {}}, headers=headers)
    listed = await guarded.get("/runs", headers=headers)
    polled = await guarded.post(
        "/queues/default/tasks/next", params={"timeout": 0}, headers=headers
    )
    events = await guarded.get("/events/latest", headers=headers)

    assert uploaded.status_code == 200
    assert run.status_code == 201
    assert listed.json()["runs"][0]["id"] == run.json()["id"]
    assert polled.status_code == 204
    assert events.status_code == 200


async def test_a_revoked_or_expired_token_is_refused(
    guarded: httpx.AsyncClient, access: Access
) -> None:
    revoked, _ = await access.create_token("revoked")
    brief, _ = await access.create_token("brief", expires_seconds=0.1)
    await access.revoke_token("revoked")
    await asyncio.sleep(0.2)

    for secret in (revoked, brief):
        response = await guarded.get("/runs", headers=bearer(secret))
        assert response.status_code == 401


async def test_the_token_is_checked_before_the_request_is_read(
    guarded: httpx.AsyncClient,
) -> None:
    malformed = await guarded.post("/flows", content=b"{not json", headers=JSON)
    bad_id = await guarded.get("/runs/not-a-uuid")
    bad_query = await guarded.get("/runs", params={"limit": "many"})

    for response in (malformed, bad_id, bad_query):
        assert (response.status_code, _error(response)) == (401, "AuthenticationError")


async def test_what_holds_no_data_is_open_without_a_token(
    guarded: httpx.AsyncClient,
) -> None:
    health = await guarded.get("/health")
    root = await guarded.get("/")
    page = await guarded.get("/ui/")
    schema = await guarded.get("/openapi.json")

    assert health.status_code == 200
    assert root.status_code == 307
    assert page.status_code == 200
    assert schema.status_code == 200
    assert "HTTPBearer" in schema.json()["components"]["securitySchemes"]


async def test_the_documentation_pages_are_served_only_without_authentication(
    guarded: httpx.AsyncClient, open_app: httpx.AsyncClient
) -> None:
    for path in ("/docs", "/redoc"):
        assert (await guarded.get(path)).status_code == 404
        assert (await open_app.get(path)).status_code == 200


PUBLIC = {
    "/health",
    "/auth/session",
    "/auth/logout",
}
"""What a person with no session reaches: to sign in, and out."""


async def test_every_route_in_the_schema_but_the_public_ones_needs_a_token(
    guarded: httpx.AsyncClient,
) -> None:
    """Asked of the app, not of its router: a route added anywhere is covered."""
    schema = (await guarded.get("/openapi.json")).json()
    samples = {"run_id": str(uuid.uuid4()), "task_id": str(uuid.uuid4())}
    operations = [
        (method.upper(), path)
        for path, item in schema["paths"].items()
        for method in item
    ]
    assert len(operations) > 20

    for method, path in operations:
        url = re.sub(
            r"{(\w+)}", lambda m: samples.get(m.group(1), "sample"), path
        ).replace("/versions/sample", "/versions/1.0.0")
        response = await guarded.request(method, url, headers=JSON)
        if path in PUBLIC:
            assert response.status_code != 401, f"{method} {path}"
        else:
            assert response.status_code == 401, f"{method} {path}"


# Writes from other sites, with authentication off.


@pytest.mark.parametrize("site", ["cross-site", "same-site", "Cross-Site"])
async def test_a_write_a_page_on_another_site_sent_is_refused(
    open_app: httpx.AsyncClient, site: str
) -> None:
    await open_app.post("/flows", json={"flows": [FLOW]})
    run = await open_app.post("/flows/f/runs", json={"inputs": {}})

    cancelled = await open_app.post(
        f"/runs/{run.json()['id']}/cancel",
        headers={"sec-fetch-site": site, **JSON},
    )

    assert (cancelled.status_code, _error(cancelled)) == (403, "CrossSiteRequestError")
    status = (await open_app.get(f"/runs/{run.json()['id']}")).json()["status"]
    assert status == "active"


@pytest.mark.parametrize("site", ["same-origin", "none"])
async def test_a_write_from_the_page_itself_or_typed_in_is_taken(
    open_app: httpx.AsyncClient, site: str
) -> None:
    response = await open_app.post(
        "/flows", json={"flows": [FLOW]}, headers={"sec-fetch-site": site}
    )

    assert response.status_code == 200


async def test_a_read_is_not_refused_for_where_it_came_from(
    open_app: httpx.AsyncClient,
) -> None:
    response = await open_app.get("/runs", headers={"sec-fetch-site": "cross-site"})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "content_type",
    [None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"],
)
async def test_a_write_a_form_could_send_is_refused_with_no_body_or_fetch_metadata(
    open_app: httpx.AsyncClient, content_type: str | None
) -> None:
    """What a browser that sends no Sec-Fetch-Site lets any page send."""
    await open_app.post("/flows", json={"flows": [FLOW]})
    run = await open_app.post("/flows/f/runs", json={"inputs": {}})
    headers = {} if content_type is None else {"content-type": content_type}

    cancelled = await open_app.post(f"/runs/{run.json()['id']}/cancel", headers=headers)
    polled = await open_app.post(
        "/queues/default/tasks/next",
        params={"timeout": 0, "lease_seconds": 1e9},
        headers=headers,
    )
    started = await open_app.post(f"/tasks/{uuid.uuid4()}/started", headers=headers)
    uploaded = await open_app.post("/flows", content=b'{"flows": []}', headers=headers)

    for response in (cancelled, polled, started, uploaded):
        assert (response.status_code, _error(response)) == (
            415,
            "UnsupportedMediaTypeError",
        )
    status = (await open_app.get(f"/runs/{run.json()['id']}")).json()["status"]
    assert status == "active"


async def test_json_with_parameters_is_json(open_app: httpx.AsyncClient) -> None:
    response = await open_app.post(
        "/flows",
        content=b'{"flows": []}',
        headers={"content-type": "Application/JSON; charset=utf-8"},
    )

    assert response.status_code == 200


async def test_a_cross_site_write_is_refused_before_its_token_is_looked_at(
    guarded: httpx.AsyncClient,
) -> None:
    response = await guarded.post(
        "/flows", json={"flows": []}, headers={"sec-fetch-site": "cross-site"}
    )

    assert (response.status_code, _error(response)) == (403, "CrossSiteRequestError")
