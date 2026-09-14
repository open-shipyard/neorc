# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The web UI served next to the API, from a stand-in assets directory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from neorc.manager import create_app
from neorc.manager._ui import CONTENT_SECURITY_POLICY
from neorc_core import Manager


@pytest.fixture
def static(tmp_path: Path) -> Path:
    """What a build leaves: an index.html naming a hashed asset."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(
        '<!doctype html><script type="module" src="./assets/index-abc123.js">'
        "</script><div id=root></div>"
    )
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log('ui')")
    return tmp_path


@pytest.fixture
async def http(manager: Manager, static: Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(manager, ui=static)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://manager.test"
    ) as client:
        yield client


async def test_the_ui_is_served_at_its_prefix_and_the_root_leads_there(
    http: httpx.AsyncClient,
) -> None:
    root = await http.get("/")
    bare = await http.get("/ui")
    index = await http.get("/ui/")
    asset = await http.get("/ui/assets/index-abc123.js")
    missing = await http.get("/ui/assets/nothing.js")

    assert root.status_code == 307 and root.headers["location"] == "/ui/"
    assert bare.status_code == 307 and bare.headers["location"].endswith("/ui/")
    assert index.status_code == 200
    assert index.text.startswith("<!doctype html>")
    assert asset.status_code == 200 and asset.text == "console.log('ui')"
    assert missing.status_code == 404


async def test_hashed_assets_are_immutable_and_the_page_is_not(
    http: httpx.AsyncClient,
) -> None:
    index = await http.get("/ui/")
    asset = await http.get("/ui/assets/index-abc123.js")
    missing = await http.get("/ui/assets/index-newhash.js")

    assert index.headers["cache-control"] == "no-cache"
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    # A 404 kept as immutable would outlive the deploy that adds the file.
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "no-cache"


async def test_every_ui_response_carries_the_security_headers(
    http: httpx.AsyncClient,
) -> None:
    for path in ("/ui/", "/ui/assets/index-abc123.js", "/ui/assets/nothing.js"):
        response = await http.get(path)

        assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "same-origin"

    api = await http.get("/health")
    assert "content-security-policy" not in api.headers


def test_the_policy_keeps_scripts_and_connections_at_home() -> None:
    directives = dict(d.split(" ", 1) for d in CONTENT_SECURITY_POLICY.split("; "))

    assert directives["default-src"] == "'none'"
    assert directives["script-src"] == "'self'"
    assert directives["connect-src"] == "'self'"
    assert directives["frame-ancestors"] == "'none'"
    assert "'unsafe-inline'" not in directives["script-src"]


async def test_without_a_ui_the_api_stands_alone(manager: Manager) -> None:
    app = create_app(manager)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://manager.test"
    ) as http:
        assert (await http.get("/")).status_code == 404
        assert (await http.get("/ui/")).status_code == 404
        assert (await http.get("/health")).status_code == 200


def test_a_directory_without_index_html_is_refused(
    manager: Manager, tmp_path: Path
) -> None:
    with pytest.raises(FileNotFoundError, match=r"index\.html"):
        create_app(manager, ui=tmp_path)
