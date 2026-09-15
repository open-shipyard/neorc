# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Signing in to the manager, over HTTP, against a stand-in OpenID provider.

The manager on the memory stores and the provider each on an ASGI transport;
a test client plays the browser, following redirects by hand and keeping the
cookies. What an ID token must hold is tested on the client alone; here, what
the routes add: the cookies, the state, the redirects, the session a request
carries, and the Origin check on its writes.
"""

from __future__ import annotations

import asyncio
import tomllib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from conftest import StandInProvider

from neorc.auth import parse_auth_config
from neorc.manager import create_app
from neorc.manager._schemas import SessionResponse
from neorc.manager._sign_in import SignIn
from neorc_core import Access, Manager
from neorc_core.local import MemoryCredentialStore

FLOW = {"name": "f", "version": "1.0.0", "steps": {"work": {"handler": "m:work"}}}
JSON = {"content-type": "application/json"}
ISSUER = "https://idp.example.com"


def auth_toml(public_url: str, allow: str = 'email = "ada@example.com"') -> str:
    return f"""
    public_url = "{public_url}"

    [providers.stand-in]
    title = "Stand-in"
    issuer = "{ISSUER}"
    client_id = "neorc-client"
    client_secret_env = "STAND_IN_SECRET"

    [[allow]]
    provider = "stand-in"
    {allow}
    """


@dataclass
class Deployment:
    manager: httpx.AsyncClient
    """The browser, at the public URL, keeping cookies."""
    provider: httpx.AsyncClient
    stand_in: StandInProvider
    access: Access
    public_url: str
    transport: httpx.ASGITransport
    """The manager's, for a client with no cookies."""


async def deploy(
    manager: Manager, public_url: str, **toml: str
) -> AsyncIterator[Deployment]:
    config = parse_auth_config(
        tomllib.loads(auth_toml(public_url, **toml)),
        {"STAND_IN_SECRET": "stand-in-secret"},
    )
    stand_in = StandInProvider(ISSUER)
    access = Access(
        MemoryCredentialStore(),
        allow=config.allow,
        session_seconds=config.session_seconds,
    )
    sign_in = SignIn.build(
        config, access, transport=httpx.ASGITransport(app=stand_in.app)
    )
    app = create_app(manager, access=access, sign_in=sign_in, long_poll_timeout=1)
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url=public_url) as browser,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=stand_in.app)) as idp,
    ):
        yield Deployment(browser, idp, stand_in, access, public_url, transport)


@pytest.fixture
async def deployment(manager: Manager) -> AsyncIterator[Deployment]:
    async for built in deploy(manager, "http://127.0.0.1:8420"):
        yield built


@pytest.fixture
async def secure(manager: Manager) -> AsyncIterator[Deployment]:
    async for built in deploy(manager, "https://neorc.example.com"):
        yield built


async def begin(d: Deployment) -> httpx.Response:
    """The browser follows the sign-in link to the provider, and back: the callback."""
    login = await d.manager.get("/auth/login/stand-in")
    assert login.status_code == 303, login.text
    to_provider = login.headers["location"]
    assert to_provider.startswith(f"{ISSUER}/authorize?")
    back = await d.provider.get(to_provider)
    return back


async def signed_in(d: Deployment) -> httpx.Response:
    back = await begin(d)
    return await d.manager.get(back.headers["location"])


def error_of(response: httpx.Response) -> str:
    location = response.headers["location"]
    fragment = urlsplit(location).fragment
    assert fragment.startswith("/sign-in?")
    return parse_qs(fragment.partition("?")[2])["error"][0]


# A whole sign-in.


async def test_a_person_signs_in_and_their_session_reaches_the_api(
    deployment: Deployment,
) -> None:
    before = await deployment.manager.get("/auth/session")

    done = await signed_in(deployment)
    session = await deployment.manager.get("/auth/session")
    flows = await deployment.manager.get("/flows")

    assert before.json() == {
        "authentication": True,
        "public_url": "http://127.0.0.1:8420",
        "providers": [{"id": "stand-in", "title": "Stand-in"}],
        "principal": None,
    }
    assert done.status_code == 303
    assert done.headers["location"] == "http://127.0.0.1:8420/ui/"
    assert done.headers["cache-control"] == "no-store"
    assert session.json()["principal"] == {
        "kind": "session",
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "provider": "stand-in",
    }
    SessionResponse.model_validate(session.json())
    SessionResponse.model_validate(before.json())
    assert flows.status_code == 200
    (asked,) = deployment.stand_in.authorized
    assert asked["redirect_uri"] == "http://127.0.0.1:8420/auth/callback/stand-in"


async def test_the_cookies_are_host_only_secure_and_not_for_scripts_over_https(
    secure: Deployment,
) -> None:
    login = await secure.manager.get("/auth/login/stand-in")
    back = await secure.provider.get(login.headers["location"])
    done = await secure.manager.get(back.headers["location"])

    state_cookie = login.headers["set-cookie"]
    session_cookies = done.headers.get_list("set-cookie")
    for cookie in (state_cookie, *session_cookies):
        attributes = {part.strip().lower() for part in cookie.split(";")}
        assert cookie.startswith("__Host-neorc_")
        assert {"path=/", "secure", "httponly", "samesite=lax"} <= attributes
        assert not any(a.startswith("domain=") for a in attributes)
    assert done.headers["location"] == "https://neorc.example.com/ui/"
    assert any(c.startswith("__Host-neorc_session=") for c in session_cookies)
    assert any(
        c.startswith("__Host-neorc_login=") and "max-age=0" in c.lower()
        for c in session_cookies
    )


async def test_over_loopback_http_the_cookies_cannot_be_prefixed(
    deployment: Deployment,
) -> None:
    login = await deployment.manager.get("/auth/login/stand-in")

    cookie = login.headers["set-cookie"]
    assert cookie.startswith("neorc_login=")
    assert "secure" not in cookie.lower()


async def test_signing_out_ends_the_session(deployment: Deployment) -> None:
    await signed_in(deployment)
    origin = {"origin": deployment.public_url, **JSON}

    out = await deployment.manager.post("/auth/logout", headers=origin)
    after = await deployment.manager.get("/flows")
    session = await deployment.manager.get("/auth/session")

    assert out.status_code == 204
    assert "neorc_session=" in out.headers["set-cookie"]
    assert after.status_code == 401
    assert session.json()["principal"] is None


async def test_an_ended_session_is_refused_even_if_the_browser_kept_it(
    deployment: Deployment,
) -> None:
    await signed_in(deployment)
    kept = deployment.manager.cookies.get("neorc_session")
    assert kept

    await deployment.access.end_sessions()
    deployment.manager.cookies.clear()
    deployment.manager.cookies.set("neorc_session", kept)
    refused = await deployment.manager.get("/flows")

    assert refused.status_code == 401


# The writes of a session.


async def test_a_session_writes_only_from_a_page_on_the_public_url(
    deployment: Deployment,
) -> None:
    await signed_in(deployment)
    same_origin = {"origin": "http://127.0.0.1:8420", **JSON}

    uploaded = await deployment.manager.post(
        "/flows", json={"flows": [FLOW]}, headers=same_origin
    )
    started = await deployment.manager.post(
        "/flows/f/runs", json={"inputs": {}}, headers={"sec-fetch-site": "same-origin"}
    )
    other_origin = await deployment.manager.post(
        f"/runs/{started.json()['id']}/cancel",
        headers={"origin": "http://localhost:8420", **JSON},
    )
    no_origin = await deployment.manager.post(
        f"/runs/{started.json()['id']}/cancel", headers=JSON
    )
    null_origin = await deployment.manager.post(
        f"/runs/{started.json()['id']}/cancel", headers={"origin": "null", **JSON}
    )
    logout_elsewhere = await deployment.manager.post(
        "/auth/logout", headers={"origin": "https://evil.example.com", **JSON}
    )

    assert uploaded.status_code == 200
    assert started.status_code == 201
    for refused in (other_origin, no_origin, null_origin, logout_elsewhere):
        assert refused.status_code == 403
        assert refused.json()["error"] == "CrossSiteRequestError"
    assert (await deployment.manager.get("/flows")).status_code == 200


async def test_a_session_works_behind_a_proxy_that_rewrites_the_host(
    deployment: Deployment,
) -> None:
    """The manager compares Origin with the public URL, never with Host."""
    await signed_in(deployment)
    proxied = {"host": "manager.internal:8000"}

    read = await deployment.manager.get("/flows", headers=proxied)
    write = await deployment.manager.post(
        "/flows",
        json={"flows": [FLOW]},
        headers={**proxied, "origin": deployment.public_url},
    )

    assert (read.status_code, write.status_code) == (200, 200)


async def test_a_token_s_writes_need_no_origin(deployment: Deployment) -> None:
    secret, _ = await deployment.access.create_token("ci")

    async with httpx.AsyncClient(
        transport=deployment.transport, base_url=deployment.public_url
    ) as machine:
        uploaded = await machine.post(
            "/flows",
            json={"flows": [FLOW]},
            headers={"authorization": f"Bearer {secret}"},
        )

    assert uploaded.status_code == 200


async def test_a_request_with_both_a_token_and_a_session_is_refused(
    deployment: Deployment,
) -> None:
    await signed_in(deployment)
    secret, _ = await deployment.access.create_token("ci")

    both = await deployment.manager.get(
        "/flows", headers={"authorization": f"Bearer {secret}"}
    )

    assert both.status_code == 401
    assert "not both" in both.json()["detail"]


# Sign-ins that do not end in a session.


async def test_a_callback_in_a_browser_that_did_not_begin_it_is_refused(
    deployment: Deployment,
) -> None:
    back = await begin(deployment)
    deployment.manager.cookies.clear()

    refused = await deployment.manager.get(back.headers["location"])

    assert error_of(refused) == "state_mismatch"
    assert (await deployment.manager.get("/flows")).status_code == 401


@pytest.mark.parametrize("state", ["é", "%E9", "\u2603" * 43])
async def test_a_state_of_any_text_is_a_mismatch_not_a_crash(
    deployment: Deployment, state: str
) -> None:
    await begin(deployment)  # the login cookie is set

    refused = await deployment.manager.get(
        "/auth/callback/stand-in", params={"state": state, "code": "c"}
    )

    assert refused.status_code == 303
    assert error_of(refused) == "state_mismatch"


async def test_a_callback_with_another_sign_in_s_state_is_refused(
    deployment: Deployment,
) -> None:
    first = await begin(deployment)
    await begin(deployment)  # this browser's cookie now holds the second state

    refused = await deployment.manager.get(first.headers["location"])

    assert error_of(refused) == "state_mismatch"


async def test_a_state_is_used_once(deployment: Deployment) -> None:
    back = await begin(deployment)
    login_cookie = deployment.manager.cookies.get("neorc_login")
    await deployment.manager.get(back.headers["location"])
    deployment.manager.cookies.clear()

    deployment.manager.cookies.set("neorc_login", login_cookie or "")
    again = await deployment.manager.get(back.headers["location"])

    assert error_of(again) == "expired"


async def test_a_sign_in_that_took_too_long_is_refused(manager: Manager) -> None:
    async for d in deploy(manager, "http://127.0.0.1:8420"):
        d.access._login_seconds = 0.1
        back = await begin(d)
        state = d.manager.cookies.get("neorc_login") or ""
        await asyncio.sleep(0.2)
        # The cookie lives whole seconds; the pending login, what the store says.
        d.manager.cookies.set("neorc_login", state)

        refused = await d.manager.get(back.headers["location"])

        assert error_of(refused) == "expired"


async def test_a_person_the_allow_list_does_not_name_is_not_let_in(
    deployment: Deployment,
) -> None:
    deployment.stand_in.person["email"] = "eve@example.org"

    refused = await signed_in(deployment)

    assert error_of(refused) == "not_allowed"
    assert (await deployment.manager.get("/flows")).status_code == 401
    assert "neorc_session=" not in refused.headers.get("set-cookie", "")


async def test_a_provider_that_refuses_or_tampers_is_told_in_a_code_alone(
    deployment: Deployment,
) -> None:
    back = await begin(deployment)
    query = parse_qs(urlsplit(back.headers["location"]).query)
    refused = await deployment.manager.get(
        "/auth/callback/stand-in",
        params={"state": query["state"][0], "error": "access_denied"},
    )
    deployment.stand_in.tamper = {"aud": "<script>alert(1)</script>"}
    tampered = await signed_in(deployment)

    assert error_of(refused) == "provider_refused"
    assert error_of(tampered) == "invalid_id_token"
    assert "script" not in tampered.headers["location"]


async def test_an_unknown_provider_is_a_code_too(deployment: Deployment) -> None:
    login = await deployment.manager.get("/auth/login/github")
    callback = await deployment.manager.get(
        "/auth/callback/github", params={"state": "s", "code": "c"}
    )

    assert error_of(login) == "unknown_provider"
    assert error_of(callback) == "unknown_provider"


async def test_a_provider_down_at_one_sign_in_is_asked_again_at_the_next(
    deployment: Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    deployment.stand_in.unavailable = 1
    begun: list[str] = []
    original = deployment.access.begin_login

    async def recorded(provider: str) -> Any:
        begun.append(provider)
        return await original(provider)

    monkeypatch.setattr(deployment.access, "begin_login", recorded)

    down = await deployment.manager.get("/auth/login/stand-in")
    stored_while_down = list(begun)
    done = await signed_in(deployment)

    assert error_of(down) == "provider_unavailable"
    assert stored_while_down == []  # nothing kept for a sign-in that cannot go on
    assert done.headers["location"].endswith("/ui/")


async def test_past_the_limit_of_sign_ins_in_progress_a_sign_in_is_refused(
    deployment: Deployment,
) -> None:
    deployment.access._max_pending_logins = 1
    await deployment.manager.get("/auth/login/stand-in")

    refused = await deployment.manager.get("/auth/login/stand-in")

    assert error_of(refused) == "busy"


# Without sign-in.


async def test_without_sign_in_the_session_says_so_and_login_is_not_found(
    manager: Manager,
) -> None:
    for access in (None, Access(MemoryCredentialStore())):
        app = create_app(manager, access=access)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://manager.test"
        ) as http:
            session = await http.get("/auth/session")
            login = await http.get("/auth/login/google")
            logout = await http.post("/auth/logout", headers=JSON)
            http.cookies.set("neorc_session", "x")
            cookie = await http.get("/flows")

        assert session.json() == {
            "authentication": access is not None,
            "public_url": None,
            "providers": [],
            "principal": None,
        }
        assert login.status_code == 404
        assert logout.status_code == 204
        assert cookie.status_code == (401 if access is not None else 200)


def test_sign_in_must_share_the_app_s_access(manager: Manager) -> None:
    config = parse_auth_config(
        tomllib.loads(auth_toml("http://127.0.0.1:8420")),
        {"STAND_IN_SECRET": "s"},
    )
    access = Access(MemoryCredentialStore())
    sign_in = SignIn.build(config, access)

    with pytest.raises(ValueError, match="own Access"):
        create_app(manager, access=Access(MemoryCredentialStore()), sign_in=sign_in)
    with pytest.raises(ValueError, match="own Access"):
        create_app(manager, access=None, sign_in=sign_in)
