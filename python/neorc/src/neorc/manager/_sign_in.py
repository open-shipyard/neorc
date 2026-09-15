# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""What the ``/auth`` routes and the guard share about signing in.

The configuration, the ``Access`` sessions are opened on, the providers, and
the two cookies: a sign-in's ``state`` and a session's secret. Both are
``HttpOnly`` and ``SameSite=Lax`` (``Strict`` would drop the ``state`` cookie
on the provider's redirect back), and on an ``https`` public URL ``Secure``
and ``__Host-`` prefixed with ``Path=/``: the prefix forbids a ``Domain``
attribute, so a sibling subdomain can set neither. Over ``http``, which only a
loopback public URL allows, every other server on the machine shares them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request, Response

from neorc.auth._config import AuthConfig
from neorc.auth._oidc import OidcProvider
from neorc.manager._access import SAFE_METHODS
from neorc_core import Access, CrossSiteRequestError


@dataclass
class SignIn:
    """What the routes and the guard share about signing in."""

    config: AuthConfig
    access: Access
    providers: dict[str, OidcProvider] = field(default_factory=dict)

    @classmethod
    def build(cls, config: AuthConfig, access: Access, **options: Any) -> SignIn:
        providers = {
            provider_id: OidcProvider(provider, **options)
            for provider_id, provider in config.providers.items()
        }
        return cls(config, access, providers)

    @property
    def session_cookie(self) -> str:
        return "__Host-neorc_session" if self.config.secure else "neorc_session"

    @property
    def login_cookie(self) -> str:
        return "__Host-neorc_login" if self.config.secure else "neorc_login"

    def set_cookie(
        self, response: Response, name: str, value: str, max_age: float
    ) -> None:
        response.set_cookie(
            name,
            value,
            max_age=math.ceil(max_age),  # never 0, which would delete it
            path="/",
            secure=self.config.secure,
            httponly=True,
            samesite="lax",
        )

    def clear_cookie(self, response: Response, name: str) -> None:
        response.delete_cookie(
            name, path="/", secure=self.config.secure, httponly=True, samesite="lax"
        )

    def ensure_same_origin(self, request: Request) -> None:
        """Refuse a cookie-authenticated write whose page is not the public URL's.

        ``Origin`` must be the public URL's origin; a browser that sends none
        must say ``Sec-Fetch-Site: same-origin``.
        """
        if request.method in SAFE_METHODS:
            return
        origin = request.headers.get("origin")
        if origin is not None:
            if origin.lower().rstrip("/") == self.config.public_url:
                return
        elif request.headers.get("sec-fetch-site", "").lower() == "same-origin":
            return
        raise CrossSiteRequestError(
            f"{request.method} {request.url.path} with a session must come from "
            f"{self.config.public_url}, not {origin or 'an unnamed origin'}"
        )
