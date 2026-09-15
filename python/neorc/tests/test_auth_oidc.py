# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The OpenID Connect client, against a stand-in provider on an ASGI transport."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from conftest import StandInProvider

from neorc.auth import OidcProvider, ProviderConfig, SignInFailed
from neorc.auth._oidc import (
    INVALID_ID_TOKEN,
    PROVIDER_REFUSED,
    PROVIDER_UNAVAILABLE,
    pkce_challenge,
)
from neorc_core import Identity

REDIRECT = "https://neorc.example.com/auth/callback/stand-in"
NONCE = "n" * 43
VERIFIER = "v" * 64


def config(issuer: str, **changes: Any) -> ProviderConfig:
    fields: dict[str, Any] = {
        "id": "stand-in",
        "title": "Stand-in",
        "issuer": issuer,
        "client_id": "neorc-client",
        "client_secret": "stand-in-secret",
        **changes,
    }
    return ProviderConfig(**fields)


@pytest.fixture
def stand_in() -> StandInProvider:
    return StandInProvider("https://idp.example.com")


def client(stand_in: StandInProvider, **changes: Any) -> OidcProvider:
    return OidcProvider(
        config(stand_in.issuer, **changes),
        transport=httpx.ASGITransport(app=stand_in.app),
    )


async def sign_in(stand_in: StandInProvider, oidc: OidcProvider) -> Identity:
    """The browser's part: to the provider and back with a code."""
    url = await oidc.authorization_url(
        state="s", nonce=NONCE, verifier=VERIFIER, redirect_uri=REDIRECT
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=stand_in.app)) as b:
        back = await b.get(url)
    code = parse_qs(urlsplit(back.headers["location"]).query)["code"][0]
    return await oidc.identity(
        code=code, verifier=VERIFIER, nonce=NONCE, redirect_uri=REDIRECT
    )


async def test_a_sign_in_asks_for_the_code_flow_with_pkce_and_says_who(
    stand_in: StandInProvider,
) -> None:
    oidc = client(stand_in)

    identity = await sign_in(stand_in, oidc)

    (asked,) = stand_in.authorized
    assert asked["response_type"] == "code"
    assert asked["client_id"] == "neorc-client"
    assert asked["redirect_uri"] == REDIRECT
    assert asked["scope"] == "openid email profile"
    assert (asked["state"], asked["nonce"]) == ("s", NONCE)
    assert asked["code_challenge"] == pkce_challenge(VERIFIER)
    assert asked["code_challenge_method"] == "S256"
    (exchanged,) = stand_in.exchanged
    assert exchanged["code_verifier"] == VERIFIER
    assert exchanged["authorization"].startswith("Basic ")
    assert "client_secret" not in exchanged
    assert identity == Identity(
        provider="stand-in",
        subject="248289761001",
        name="Ada Lovelace",
        email="ada@example.com",
        email_verified=True,
    )


async def test_the_client_secret_can_be_posted_and_scopes_chosen(
    stand_in: StandInProvider,
) -> None:
    oidc = client(
        stand_in,
        token_endpoint_auth="client_secret_post",
        scopes=("openid", "groups"),
    )

    await sign_in(stand_in, oidc)

    assert stand_in.authorized[0]["scope"] == "openid groups"
    assert stand_in.exchanged[0]["client_secret"] == "stand-in-secret"
    assert stand_in.exchanged[0]["authorization"] == ""


async def test_a_secret_with_reserved_characters_is_form_encoded_in_basic_auth() -> (
    None
):
    stand_in = StandInProvider("https://idp.example.com", client_secret="a:b+c/d%")
    oidc = client(stand_in, client_secret="a:b+c/d%")

    identity = await sign_in(stand_in, oidc)

    assert identity.subject == "248289761001"


async def test_groups_come_from_the_configured_claim(
    stand_in: StandInProvider,
) -> None:
    stand_in.person["groups"] = ["neorc-users", "other", 7]
    stand_in.person["roles"] = "admin"

    listed = await sign_in(stand_in, client(stand_in, groups_claim="groups"))
    single = await sign_in(stand_in, client(stand_in, groups_claim="roles"))
    unconfigured = await sign_in(stand_in, client(stand_in))

    assert listed.groups == {"neorc-users", "other"}
    assert single.groups == {"admin"}
    assert unconfigured.groups == frozenset()


@pytest.mark.parametrize(
    ("tamper", "why"),
    [
        ({"iss": "https://other.example.com"}, "iss"),
        ({"aud": "someone-else"}, "aud"),
        ({"aud": ["someone-else", "neorc-client"]}, "azp"),
        ({"aud": ["neorc-client", "x"], "azp": "x"}, "azp"),
        ({"azp": "someone-else"}, "azp"),
        ({"exp": time.time() - 120}, "exp"),
        ({"exp": "tomorrow"}, "exp"),
        ({"iat": time.time() + 120}, "iat"),
        ({"iat": None}, "iat"),
        ({"nonce": "another sign-in's"}, "nonce"),
        ({"nonce": None}, "nonce"),
        ({"sub": ""}, "sub"),
    ],
)
async def test_an_id_token_not_for_this_sign_in_is_refused(
    stand_in: StandInProvider, tamper: dict[str, Any], why: str
) -> None:
    stand_in.tamper = tamper

    with pytest.raises(SignInFailed) as failed:
        await sign_in(stand_in, client(stand_in))

    assert failed.value.code == INVALID_ID_TOKEN
    assert failed.value.detail.startswith(why)


async def test_an_audience_list_with_the_client_as_its_party_is_taken(
    stand_in: StandInProvider,
) -> None:
    stand_in.tamper = {"aud": ["neorc-client", "api"], "azp": "neorc-client"}

    assert (await sign_in(stand_in, client(stand_in))).subject == "248289761001"


async def test_a_clock_a_little_off_is_forgiven(stand_in: StandInProvider) -> None:
    stand_in.tamper = {"iat": time.time() + 30, "exp": time.time() - 30}

    assert (await sign_in(stand_in, client(stand_in))).subject == "248289761001"


def test_an_id_token_that_is_no_jwt_is_refused(stand_in: StandInProvider) -> None:
    oidc = client(stand_in)

    for token in ("not-a-jwt", "a.!!!.c", "a.W10.c"):
        with pytest.raises(SignInFailed) as failed:
            oidc._checked(token, NONCE)
        assert failed.value.code == INVALID_ID_TOKEN


async def test_discovery_is_asked_once_and_a_failure_is_not_kept(
    stand_in: StandInProvider,
) -> None:
    oidc = client(stand_in)
    stand_in.unavailable = 1

    with pytest.raises(SignInFailed) as failed:
        await sign_in(stand_in, oidc)
    await sign_in(stand_in, oidc)
    stand_in.discovery = {"authorization_endpoint": "https://moved.example.com/a"}
    url = await oidc.authorization_url(
        state="s", nonce=NONCE, verifier=VERIFIER, redirect_uri=REDIRECT
    )

    assert failed.value.code == PROVIDER_UNAVAILABLE
    assert url.startswith(f"{stand_in.issuer}/authorize?")


@pytest.mark.parametrize(
    "discovery",
    [
        {"issuer": "https://impostor.example.com"},
        {"token_endpoint": "http://idp.example.com/token"},
        {"authorization_endpoint": "http://10.0.0.5/authorize"},
        {"token_endpoint": None},
    ],
)
async def test_discovery_that_cannot_be_trusted_is_refused(
    stand_in: StandInProvider, discovery: dict[str, Any]
) -> None:
    stand_in.discovery = discovery

    with pytest.raises(SignInFailed) as failed:
        await sign_in(stand_in, client(stand_in))

    assert failed.value.code == PROVIDER_UNAVAILABLE


@pytest.mark.parametrize(
    "published", ["https://tenant.example.com/", "https://Tenant.example.com:443"]
)
async def test_an_issuer_is_matched_as_the_provider_publishes_it(
    published: str,
) -> None:
    """Auth0 publishes its issuer with a trailing slash, and iss says it so."""
    stand_in = StandInProvider(published)
    oidc = OidcProvider(
        config("https://tenant.example.com"),
        transport=httpx.ASGITransport(app=stand_in.app),
    )

    identity = await sign_in(stand_in, oidc)

    assert identity.subject == "248289761001"
    stand_in.tamper = {"iss": "https://tenant.example.com/other"}
    with pytest.raises(SignInFailed, match="iss"):
        await sign_in(stand_in, oidc)


async def test_a_loopback_provider_may_be_plain_http() -> None:
    stand_in = StandInProvider("http://127.0.0.1:9000")

    assert (await sign_in(stand_in, client(stand_in))).subject == "248289761001"


async def test_a_provider_that_cannot_be_reached_is_unavailable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    oidc = OidcProvider(
        config("https://idp.example.com"), transport=httpx.MockTransport(refuse)
    )

    with pytest.raises(SignInFailed) as failed:
        await oidc.authorization_url(
            state="s", nonce=NONCE, verifier=VERIFIER, redirect_uri=REDIRECT
        )

    assert failed.value.code == PROVIDER_UNAVAILABLE


async def test_a_code_the_provider_does_not_know_is_refused(
    stand_in: StandInProvider,
) -> None:
    oidc = client(stand_in)
    await oidc.authorization_url(
        state="s", nonce=NONCE, verifier=VERIFIER, redirect_uri=REDIRECT
    )

    with pytest.raises(SignInFailed) as unknown:
        await oidc.identity(
            code="made-up", verifier=VERIFIER, nonce=NONCE, redirect_uri=REDIRECT
        )
    with pytest.raises(SignInFailed) as wrong_verifier:
        await sign_in_with_verifier(stand_in, oidc, "w" * 64)

    assert unknown.value.code == PROVIDER_REFUSED
    assert wrong_verifier.value.code == PROVIDER_REFUSED


async def sign_in_with_verifier(
    stand_in: StandInProvider, oidc: OidcProvider, verifier: str
) -> Identity:
    url = await oidc.authorization_url(
        state="s", nonce=NONCE, verifier=VERIFIER, redirect_uri=REDIRECT
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=stand_in.app)) as b:
        back = await b.get(url)
    code = parse_qs(urlsplit(back.headers["location"]).query)["code"][0]
    return await oidc.identity(
        code=code, verifier=verifier, nonce=NONCE, redirect_uri=REDIRECT
    )


async def test_a_token_endpoint_that_fails_is_unavailable(
    stand_in: StandInProvider,
) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(502, text="Bad Gateway")
        return httpx.Response(
            200,
            json={
                "issuer": stand_in.issuer,
                "authorization_endpoint": f"{stand_in.issuer}/authorize",
                "token_endpoint": f"{stand_in.issuer}/token",
            },
        )

    oidc = OidcProvider(config(stand_in.issuer), transport=httpx.MockTransport(broken))

    with pytest.raises(SignInFailed) as failed:
        await oidc.identity(
            code="c", verifier=VERIFIER, nonce=NONCE, redirect_uri=REDIRECT
        )

    assert failed.value.code == PROVIDER_UNAVAILABLE


# Google.


@pytest.fixture
def google() -> StandInProvider:
    return StandInProvider("https://accounts.google.com")


async def test_google_s_issuer_without_a_scheme_is_its_issuer(
    google: StandInProvider,
) -> None:
    google.tamper = {"iss": "accounts.google.com"}

    assert (await sign_in(google, client(google))).subject == "248289761001"


@pytest.mark.parametrize(
    ("email", "hosted_domain", "verified"),
    [
        ("ada@example.com", "example.com", True),
        ("ada@subsidiary.com", "example.com", True),  # a Workspace's other domain
        ("ada@example.com", None, False),  # a personal account on a work address
        ("ada@gmail.com", None, True),
        ("Ada@GoogleMail.com", None, True),
        ("ada@gmail.com.example.com", None, False),
    ],
)
async def test_google_verifies_an_address_only_in_a_workspace_or_at_gmail(
    google: StandInProvider,
    email: str,
    hosted_domain: str | None,
    verified: bool,
) -> None:
    google.person.update({"email": email, "email_verified": True, "hd": hosted_domain})

    identity = await sign_in(google, client(google))

    assert identity.email == email
    assert identity.hosted_domain == hosted_domain
    assert identity.email_verified is verified


async def test_an_address_google_did_not_verify_stays_unverified(
    google: StandInProvider,
) -> None:
    google.person.update({"email": "ada@gmail.com", "email_verified": False})

    assert (await sign_in(google, client(google))).email_verified is False


async def test_an_hd_claim_is_read_from_google_alone(
    stand_in: StandInProvider,
) -> None:
    stand_in.person["hd"] = "example.com"

    assert (await sign_in(stand_in, client(stand_in))).hosted_domain is None


async def test_another_provider_s_verified_address_is_taken_as_it_says(
    stand_in: StandInProvider,
) -> None:
    stand_in.person.update({"email": "ada@example.com", "email_verified": "true"})

    assert (await sign_in(stand_in, client(stand_in))).email_verified is True
    stand_in.person.pop("email_verified")
    assert (await sign_in(stand_in, client(stand_in))).email_verified is False
