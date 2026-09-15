# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Who is asking, and whether a write may come from where it came.

One dependency guards every route of the router. Before the route reads its
body, or FastAPI reads its path and query:

- A write, any method but ``GET``, ``HEAD`` and ``OPTIONS``, is refused when a
  browser says a page on another site sent it (``Sec-Fetch-Site``), and when
  it is not sent as ``application/json``: a form, or a ``fetch`` a page may
  send without a preflight, cannot carry that type, and the manager answers no
  preflight. This holds with authentication off too, where no credential
  stands between a web page and the manager. It needs every route to read its
  body through ``read_body``, never through a declared body parameter, which
  FastAPI would read before any dependency runs.
- With authentication on, the request must carry an API token as
  ``Authorization: Bearer``; its principal is the dependency's value.

Routes outside the router, ``/health`` and the UI, hold no data and are not
guarded.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from neorc_core import (
    Access,
    AuthenticationError,
    CrossSiteRequestError,
    Principal,
    UnsupportedMediaTypeError,
)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

_CROSS_SITE = frozenset({"cross-site", "same-site"})

_bearer = HTTPBearer(
    auto_error=False,
    description="An API token, from `neorc tokens create`.",
)


async def guard(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal | None:
    """The principal behind the request, or ``None`` with authentication off.

    Raises ``CrossSiteRequestError`` and ``UnsupportedMediaTypeError`` for a
    write that may not be taken, and ``AuthenticationError`` for a request
    without an accepted token.
    """
    if request.method not in SAFE_METHODS:
        _ensure_same_site_json(request)
    access: Access | None = request.app.state.access
    if access is None:
        return None
    if credentials is None:
        raise AuthenticationError(
            "this manager needs an API token, sent as 'Authorization: Bearer <token>'"
        )
    return await access.authenticate_token(credentials.credentials)


Guarded = Annotated[Principal | None, Depends(guard)]
"""The principal, injected; a module-level name, for FastAPI."""


def _ensure_same_site_json(request: Request) -> None:
    site = request.headers.get("sec-fetch-site", "").lower()
    if site in _CROSS_SITE:
        raise CrossSiteRequestError(
            f"{request.method} {request.url.path} came from a page on another site"
        )
    media_type = request.headers.get("content-type", "").partition(";")[0]
    if media_type.strip().lower() != "application/json":
        raise UnsupportedMediaTypeError(
            f"{request.method} {request.url.path} must be sent as application/json"
        )
