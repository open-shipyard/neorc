# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Serving the built web UI next to the API.

The assets come from the ``neorc-ui`` distribution and are plain files under
one path prefix; the app uses hash routing, so ``index.html`` is the only page
and no fallback route is needed. Every response under the prefix carries a
Content-Security-Policy that keeps scripts, styles and connections to the
page's own origin: a compromised bundled package can then neither load more
code nor send what it reads to another host. Inline styles are allowed, since
React and its libraries set ``style`` attributes; inline scripts are not, and
the built ``index.html`` has none.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

UI_PATH = "/ui"

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

_IMMUTABLE = b"public, max-age=31536000, immutable"
"""Hashed assets never change under their name, so a browser may keep them."""

_NO_CACHE = b"no-cache"
"""``index.html`` names the current hashes; a browser must ask each time."""


def mount_ui(app: FastAPI, static: Path, *, path: str = UI_PATH) -> None:
    """Serve the built UI in ``static`` under ``path``, and send ``/`` there.

    ``static`` must hold an ``index.html``, as ``neorc_ui.static_dir()``
    checks; the manager's tests hand in a stand-in directory.
    """
    if not (static / "index.html").is_file():
        raise FileNotFoundError(f"{static} holds no index.html to serve")
    index = f"{path}/"

    @app.get("/", include_in_schema=False)
    async def to_ui() -> RedirectResponse:
        return RedirectResponse(index)

    app.mount(path, _Headed(StaticFiles(directory=static, html=True)), name="ui")


class _Headed:
    """The static files, with the cache and security headers on every response."""

    def __init__(self, files: ASGIApp) -> None:
        self._files = files

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._files(scope, receive, send)
            return
        # The path below the mount: the scope keeps the whole one, and the
        # mount's own prefix in root_path.
        below = scope["path"].removeprefix(scope.get("root_path", ""))
        hashed = below.startswith("/assets/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # Only a hashed asset that is there is immutable: a 404 kept
                # for a year would outlive the deploy that adds the file.
                keep = hashed and message["status"] in (200, 304)
                headers.append((b"cache-control", _IMMUTABLE if keep else _NO_CACHE))
                headers.append(
                    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode())
                )
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"referrer-policy", b"same-origin"))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self._files(scope, receive, send_with_headers)
        except HTTPException as exc:
            # A missing file: answered here, so it carries the headers too,
            # rather than by the application's handler outside them.
            response = PlainTextResponse(
                str(exc.detail), status_code=exc.status_code, headers=exc.headers
            )
            await response(scope, receive, send_with_headers)
