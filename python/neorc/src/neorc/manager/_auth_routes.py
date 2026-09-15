# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Signing in to the web UI, and out: the ``/auth`` routes.

    GET  /auth/session             whether sign-in is on, with whom, and who is in
    GET  /auth/login/{provider}    start a sign-in: a redirect to the provider
    GET  /auth/callback/{provider} where the provider sends the browser back
    POST /auth/logout              end the session

The first three are public: a person with no session reaches them. Sign-out
takes a write's checks, the Origin check included, so no other site can sign
a person out.

A sign-in in progress is bound to the browser that started it by a cookie
holding its ``state``; a session is a cookie holding its secret. Both are
``HttpOnly`` and ``SameSite=Lax``, and on an ``https`` public URL ``Secure``
and ``__Host-`` prefixed, which forbids a ``Domain``: a sibling subdomain can
set neither. A failed callback redirects to the UI's sign-in page with one of
a few fixed codes, never the provider's words, and logs the detail.
"""

from __future__ import annotations

import hmac
import logging
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse

from neorc.auth._oidc import SignInFailed
from neorc.manager._access import ensure_same_site_json
from neorc.manager._schemas import SessionResponse, documented
from neorc.manager._sign_in import SignIn
from neorc_core import (
    Access,
    AuthenticationError,
    Principal,
    SignInRefusedError,
)

_log = logging.getLogger(__name__)

EXPIRED = "expired"
"""The sign-in took too long, or was used already."""
STATE_MISMATCH = "state_mismatch"
"""The callback reached a browser that did not start the sign-in."""
NOT_ALLOWED = "not_allowed"
"""The provider said who the person is, and no allow entry lets them in."""
UNKNOWN_PROVIDER = "unknown_provider"
BUSY = "busy"
"""Too many sign-ins are in progress: someone may be flooding the manager."""


def _sign_in(request: Request) -> SignIn | None:
    sign_in: SignIn | None = request.app.state.sign_in
    return sign_in


SignInState = Annotated[SignIn | None, Depends(_sign_in)]

auth_router = APIRouter(prefix="/auth", tags=["auth"])


@auth_router.get("/session", response_model=None, responses=documented(SessionResponse))
async def session(request: Request, sign_in: SignInState) -> dict[str, Any]:
    """Whether authentication is on, the providers to sign in with, and who is in."""
    access: Access | None = request.app.state.access
    principal: Principal | None = None
    providers: list[dict[str, str]] = []
    public_url = None
    if sign_in is not None:
        public_url = sign_in.config.public_url
        providers = [
            {"id": provider.id, "title": provider.title}
            for provider in sign_in.config.providers.values()
        ]
        secret = request.cookies.get(sign_in.session_cookie)
        if secret:
            try:
                principal = await sign_in.access.authenticate_session(secret)
            except AuthenticationError:
                principal = None
    return {
        "authentication": access is not None,
        "public_url": public_url,
        "providers": providers,
        "principal": _principal_body(principal),
    }


# The sign-in routes are browser navigations, not calls: out of the schema.
@auth_router.get("/login/{provider}", include_in_schema=False)
async def login(provider: str, sign_in: SignInState) -> Response:
    """Start a sign-in with ``provider``: a pending login, a cookie, a redirect."""
    if sign_in is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    oidc = sign_in.providers.get(provider)
    if oidc is None:
        return _failed(sign_in, UNKNOWN_PROVIDER, f"no provider {provider!r}")
    try:
        # Discovery first: a provider that cannot be reached leaves nothing
        # stored behind a sign-in that could never finish.
        await oidc.discover()
        state, pending = await sign_in.access.begin_login(provider)
        url = await oidc.authorization_url(
            state=state,
            nonce=pending.nonce,
            verifier=pending.verifier,
            redirect_uri=sign_in.config.redirect_uri(provider),
        )
    except SignInFailed as exc:
        return _failed(sign_in, exc.code, exc.detail)
    except AuthenticationError as exc:  # too many sign-ins in progress
        return _failed(sign_in, BUSY, str(exc))
    response = RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)
    response.headers["cache-control"] = "no-store"
    sign_in.set_cookie(
        response, sign_in.login_cookie, state, sign_in.access.login_seconds
    )
    return response


@auth_router.get("/callback/{provider}", include_in_schema=False)
async def callback(
    request: Request,
    provider: str,
    sign_in: SignInState,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    """Finish a sign-in: the state checked and taken, the code exchanged, a session."""
    if sign_in is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    oidc = sign_in.providers.get(provider)
    if oidc is None:
        return _failed(sign_in, UNKNOWN_PROVIDER, f"no provider {provider!r}")
    cookie = request.cookies.get(sign_in.login_cookie)
    # As bytes: compare_digest refuses a str holding anything but ASCII, and
    # the query and the cookie are whatever the browser was sent.
    if (
        not state
        or not cookie
        or not hmac.compare_digest(cookie.encode(), state.encode())
    ):
        return _failed(
            sign_in, STATE_MISMATCH, "the state is not the one this browser began"
        )
    try:
        pending = await sign_in.access.take_login(provider, state)
    except AuthenticationError as exc:
        return _failed(sign_in, EXPIRED, str(exc))
    if error is not None or not code:
        return _failed(
            sign_in, "provider_refused", f"the provider answered error={error!r}"
        )
    redirect_uri = sign_in.config.redirect_uri(provider)
    try:
        identity = await oidc.identity(
            code=code,
            verifier=pending.verifier,
            nonce=pending.nonce,
            redirect_uri=redirect_uri,
        )
        secret, principal = await sign_in.access.open_session(identity)
    except SignInFailed as exc:
        return _failed(sign_in, exc.code, exc.detail)
    except SignInRefusedError as exc:
        return _failed(sign_in, NOT_ALLOWED, str(exc))
    _log.info("%s signed in with %s", principal.email or principal.subject, provider)
    response = RedirectResponse(
        f"{sign_in.config.public_url}/ui/", status_code=status.HTTP_303_SEE_OTHER
    )
    response.headers["cache-control"] = "no-store"
    sign_in.clear_cookie(response, sign_in.login_cookie)
    sign_in.set_cookie(
        response, sign_in.session_cookie, secret, sign_in.access.session_seconds
    )
    return response


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, sign_in: SignInState) -> Response:
    """End the session, if any, and clear its cookie."""
    ensure_same_site_json(request)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    if sign_in is None:
        return response
    sign_in.ensure_same_origin(request)
    secret = request.cookies.get(sign_in.session_cookie)
    if secret:
        await sign_in.access.end_session(secret)
    sign_in.clear_cookie(response, sign_in.session_cookie)
    return response


def _failed(sign_in: SignIn, code: str, detail: str) -> Response:
    _log.warning("sign-in failed, %s: %s", code, detail)
    response = RedirectResponse(
        f"{sign_in.config.public_url}/ui/#/sign-in?error={quote(code)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.headers["cache-control"] = "no-store"
    sign_in.clear_cookie(response, sign_in.login_cookie)
    return response


def _principal_body(principal: Principal | None) -> dict[str, Any] | None:
    if principal is None:
        return None
    return {
        "kind": principal.kind.value,
        "name": principal.name,
        "email": principal.email,
        "provider": principal.provider,
    }
