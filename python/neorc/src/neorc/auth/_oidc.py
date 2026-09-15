# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Signing in with an OpenID Connect provider: the authorization code flow.

Discovery happens at a provider's first sign-in, not at startup, so a provider
that is down keeps no manager from serving tokens; a success is kept for the
life of the process, a failure is not. The authorization request carries
``state``, ``nonce`` and a PKCE ``S256`` challenge.

The ID token comes from the token endpoint's response, over TLS, so its
signature is not checked, as OpenID Connect Core 3.1.3.7 item 6 allows for
this flow; its claims are: ``iss``, ``aud``, ``azp``, ``exp``, ``iat`` and
``nonce``. That is sound only if the token response does come over TLS, so
the endpoints discovery names must be ``https``, loopback excepted.

Every failure is a ``SignInFailed`` with a fixed code, which is all a browser
is told; the detail is for the log.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx

from neorc.auth._config import (
    GOOGLE_ISSUER,
    ProviderConfig,
    is_loopback,
    normalise_issuer,
)
from neorc_core import Identity

CLOCK_SKEW_SECONDS = 60.0
"""How far ``exp`` and ``iat`` may be off the manager's clock."""

GMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})

_TIMEOUT = httpx.Timeout(10.0)


class SignInFailed(Exception):
    """A sign-in that cannot complete. ``code`` is what the browser is told."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


PROVIDER_UNAVAILABLE = "provider_unavailable"
"""Discovery or the token endpoint could not be reached, or answered wrongly."""
PROVIDER_REFUSED = "provider_refused"
"""The provider refused the sign-in, or the code."""
INVALID_ID_TOKEN = "invalid_id_token"
"""The ID token is not one for this sign-in."""


def pkce_challenge(verifier: str) -> str:
    """The ``S256`` code challenge for a PKCE verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OidcProvider:
    """One configured provider, reached over httpx."""

    def __init__(
        self,
        config: ProviderConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self._transport = transport
        self._clock = clock
        self._discovery: Mapping[str, Any] | None = None
        self._published_issuers: tuple[str, ...] = ()
        self._discovering = asyncio.Lock()

    async def discover(self) -> None:
        """Make sure the provider's endpoints are known; ``SignInFailed`` if not."""
        await self._discovered()

    async def authorization_url(
        self, *, state: str, nonce: str, verifier: str, redirect_uri: str
    ) -> str:
        """Where to send the browser to sign in."""
        endpoint = (await self._discovered())["authorization_endpoint"]
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": redirect_uri,
                "scope": " ".join(self.config.scopes),
                "state": state,
                "nonce": nonce,
                "code_challenge": pkce_challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
        separator = "&" if urlsplit(endpoint).query else "?"
        return f"{endpoint}{separator}{query}"

    async def identity(
        self, *, code: str, verifier: str, nonce: str, redirect_uri: str
    ) -> Identity:
        """Exchange ``code`` for an ID token, check it, and say who signed in."""
        endpoint = (await self._discovered())["token_endpoint"]
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }
        headers = {"accept": "application/json"}
        if self.config.token_endpoint_auth == "client_secret_post":
            form["client_id"] = self.config.client_id
            form["client_secret"] = self.config.client_secret
        else:
            # RFC 6749 2.3.1: each part form-encoded before it is joined.
            pair = (
                f"{quote(self.config.client_id, safe='')}:"
                f"{quote(self.config.client_secret, safe='')}"
            )
            headers["authorization"] = "Basic " + base64.b64encode(
                pair.encode("utf-8")
            ).decode("ascii")
        try:
            async with self._http() as http:
                response = await http.post(endpoint, data=form, headers=headers)
        except httpx.HTTPError as exc:
            raise SignInFailed(
                PROVIDER_UNAVAILABLE, f"token endpoint {endpoint}: {exc}"
            ) from None
        body = _json_object(response)
        if response.status_code >= 500 or body is None:
            raise SignInFailed(
                PROVIDER_UNAVAILABLE,
                f"token endpoint answered {response.status_code}: "
                f"{response.text[:200]}",
            )
        if response.status_code != 200 or "error" in body:
            raise SignInFailed(
                PROVIDER_REFUSED,
                f"token endpoint refused the code: {response.status_code} "
                f"{body.get('error')!r} {body.get('error_description')!r}",
            )
        id_token = body.get("id_token")
        if not isinstance(id_token, str):
            raise SignInFailed(INVALID_ID_TOKEN, "the token response has no id_token")
        claims = self._checked(id_token, nonce)
        return self._identity(claims)

    def _checked(self, id_token: str, nonce: str) -> Mapping[str, Any]:
        claims = _claims(id_token)
        # OpenID Connect compares iss exactly with the issuer the provider
        # publishes, which may differ from the normalised configured form by a
        # trailing slash, a default port or case.
        issuers = {self.config.issuer, *self._published_issuers}
        if self.config.issuer == GOOGLE_ISSUER:
            issuers.add("accounts.google.com")  # Google documents both forms
        issuer = claims.get("iss")
        # A str first: a list or an object cannot be looked up in a set.
        if not isinstance(issuer, str) or issuer not in issuers:
            raise SignInFailed(INVALID_ID_TOKEN, f"iss {issuer!r}")
        audience = claims.get("aud")
        audiences = [audience] if isinstance(audience, str) else audience
        if not isinstance(audiences, list) or self.config.client_id not in audiences:
            raise SignInFailed(INVALID_ID_TOKEN, f"aud {audience!r}")
        azp = claims.get("azp")
        if (len(audiences) > 1 or azp is not None) and azp != self.config.client_id:
            raise SignInFailed(INVALID_ID_TOKEN, f"azp {azp!r}")
        now = self._clock()
        expires, issued = _number(claims.get("exp")), _number(claims.get("iat"))
        if expires is None or expires + CLOCK_SKEW_SECONDS <= now:
            raise SignInFailed(
                INVALID_ID_TOKEN, f"exp {claims.get('exp')!r}, now {now}"
            )
        if issued is None or issued - CLOCK_SKEW_SECONDS > now:
            raise SignInFailed(
                INVALID_ID_TOKEN, f"iat {claims.get('iat')!r}, now {now}"
            )
        got = claims.get("nonce")
        # As bytes: compare_digest refuses a str holding anything but ASCII.
        if not isinstance(got, str) or not hmac.compare_digest(
            got.encode(), nonce.encode()
        ):
            raise SignInFailed(INVALID_ID_TOKEN, "nonce is not this sign-in's")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise SignInFailed(INVALID_ID_TOKEN, f"sub {subject!r}")
        return claims

    def _identity(self, claims: Mapping[str, Any]) -> Identity:
        email = claims.get("email")
        email = email if isinstance(email, str) and email else None
        verified = claims.get("email_verified") in (True, "true")
        # hd is Google's, and means a Workspace only there: another provider
        # may send a claim of that name holding anything, even what its users
        # type in, so it is not read from one.
        hosted_domain = claims.get("hd") if self.config.is_google else None
        hosted_domain = (
            hosted_domain if isinstance(hosted_domain, str) and hosted_domain else None
        )
        if self.config.issuer == GOOGLE_ISSUER and email is not None:
            # Google verifies personal accounts registered with any address:
            # an address is someone's only in a Workspace, whose domains its
            # administrators verified, or at Gmail, which never reassigns one.
            domain = email.rpartition("@")[2].lower()
            verified = verified and (
                hosted_domain is not None or domain in GMAIL_DOMAINS
            )
        groups: frozenset[str] = frozenset()
        if self.config.groups_claim is not None:
            raw = claims.get(self.config.groups_claim)
            if isinstance(raw, str):
                groups = frozenset({raw})
            elif isinstance(raw, list):
                groups = frozenset(g for g in raw if isinstance(g, str))
        name = claims.get("name")
        return Identity(
            provider=self.config.id,
            subject=claims["sub"],
            name=name if isinstance(name, str) and name else None,
            email=email,
            email_verified=verified,
            hosted_domain=hosted_domain,
            groups=groups,
        )

    async def _discovered(self) -> Mapping[str, Any]:
        if self._discovery is not None:
            return self._discovery
        async with self._discovering:
            if self._discovery is None:
                self._discovery = await self._discover()
            return self._discovery

    async def _discover(self) -> Mapping[str, Any]:
        url = f"{self.config.issuer}/.well-known/openid-configuration"
        try:
            async with self._http() as http:
                response = await http.get(url, headers={"accept": "application/json"})
        except httpx.HTTPError as exc:
            raise SignInFailed(
                PROVIDER_UNAVAILABLE, f"discovery {url}: {exc}"
            ) from None
        document = _json_object(response)
        if response.status_code != 200 or document is None:
            raise SignInFailed(
                PROVIDER_UNAVAILABLE, f"discovery {url} answered {response.status_code}"
            )
        issuer = document.get("issuer")
        if not isinstance(issuer, str) or _normalised(issuer) != self.config.issuer:
            raise SignInFailed(
                PROVIDER_UNAVAILABLE,
                f"discovery names issuer {issuer!r}, not {self.config.issuer!r}",
            )
        for key in ("authorization_endpoint", "token_endpoint"):
            endpoint = document.get(key)
            if not isinstance(endpoint, str) or not _protected(endpoint):
                raise SignInFailed(
                    PROVIDER_UNAVAILABLE,
                    f"discovery names {key} {endpoint!r}: not https",
                )
        self._published_issuers = (issuer,)
        return document

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport, timeout=_TIMEOUT, follow_redirects=False
        )


def _normalised(issuer: str) -> str | None:
    try:
        return normalise_issuer(issuer)
    except ValueError:
        return None


def _protected(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme == "https":
        return bool(parts.hostname)
    return parts.scheme == "http" and is_loopback(parts.hostname or "")


def _claims(id_token: str) -> Mapping[str, Any]:
    pieces = id_token.split(".")
    if len(pieces) != 3:
        raise SignInFailed(INVALID_ID_TOKEN, "the id_token is not a JWT")
    try:
        payload = base64.urlsafe_b64decode(pieces[1] + "=" * (-len(pieces[1]) % 4))
        claims = json.loads(payload)
    except (binascii.Error, ValueError) as exc:
        raise SignInFailed(
            INVALID_ID_TOKEN, f"the id_token cannot be read: {exc}"
        ) from None
    if not isinstance(claims, dict):
        raise SignInFailed(INVALID_ID_TOKEN, "the id_token's claims are not an object")
    return claims


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)
