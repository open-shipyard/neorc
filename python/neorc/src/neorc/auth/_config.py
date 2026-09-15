# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""``auth.toml``: the public URL, the identity providers, and who may sign in.

    public_url = "https://neorc.example.com"
    session_hours = 12

    [providers.google]
    title = "Google"
    issuer = "https://accounts.google.com"
    client_id = "1234.apps.googleusercontent.com"
    client_secret_env = "NEORC_GOOGLE_CLIENT_SECRET"

    [[allow]]
    provider = "google"
    hosted_domain = "example.com"

Secrets are never in the file, only the name of the variable holding each.
Every problem is listed at once, as flow files' are. URLs are normalised as
they are read, so each has one form: the one redirect URIs are built from,
``Origin`` is compared with, and an ID token's ``iss`` must match.
"""

from __future__ import annotations

import ipaddress
import math
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from neorc_core import Allow, Matcher
from neorc_core._access import DEFAULT_SESSION_SECONDS, MAX_SECONDS

GOOGLE_ISSUER = "https://accounts.google.com"

DEFAULT_SCOPES = ("openid", "email", "profile")

TOKEN_ENDPOINT_AUTH = ("client_secret_basic", "client_secret_post")

_PROVIDER_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")

_DEFAULT_PORTS = {"http": 80, "https": 443}


class AuthConfigError(ValueError):
    """The configuration cannot be used. ``problems`` lists every one found."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("invalid authentication config:\n" + "\n".join(problems))


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """One OpenID Connect provider people sign in with."""

    id: str
    title: str
    issuer: str
    """Normalised: lower-case scheme and host, no default port, no trailing slash."""
    client_id: str
    client_secret: str
    scopes: tuple[str, ...] = DEFAULT_SCOPES
    groups_claim: str | None = None
    token_endpoint_auth: str = "client_secret_basic"

    @property
    def is_google(self) -> bool:
        return self.issuer == GOOGLE_ISSUER


@dataclass(frozen=True, slots=True)
class AuthConfig:
    """Sign-in for a manager: where it is served, with whom, for whom."""

    public_url: str
    """The manager's origin, normalised: ``scheme://host[:port]``."""
    session_seconds: float
    providers: Mapping[str, ProviderConfig]
    allow: tuple[Allow, ...]

    @property
    def secure(self) -> bool:
        """Whether the public URL is ``https``, which cookies are marked for."""
        return self.public_url.startswith("https://")

    def redirect_uri(self, provider: str) -> str:
        """Where a provider sends the browser back to; registered with it."""
        return f"{self.public_url}/auth/callback/{provider}"


def load_auth_config(
    path: Path, environ: Mapping[str, str] | None = None
) -> AuthConfig:
    """Read ``path``; ``AuthConfigError`` listing every problem, or ``OSError``."""
    with path.open("rb") as file:
        try:
            data = tomllib.load(file)
        except tomllib.TOMLDecodeError as exc:
            raise AuthConfigError([f"{path}: not TOML: {exc}"]) from None
    return parse_auth_config(data, os.environ if environ is None else environ)


def parse_auth_config(
    data: Mapping[str, Any], environ: Mapping[str, str]
) -> AuthConfig:
    """The configuration from its TOML structure, with secrets from ``environ``."""
    problems: list[str] = []
    _unknown(data, {"public_url", "session_hours", "providers", "allow"}, "", problems)

    public_url = ""
    raw_url = data.get("public_url")
    if not isinstance(raw_url, str):
        problems.append("public_url: missing, or not a string")
    else:
        try:
            public_url = normalise_origin(raw_url)
        except ValueError as exc:
            problems.append(f"public_url: {exc}")

    session_seconds = DEFAULT_SESSION_SECONDS
    if "session_hours" in data:
        hours = data["session_hours"]
        if (
            isinstance(hours, bool)
            or not isinstance(hours, int | float)
            or not math.isfinite(hours)
            or not 0 < hours * 3600 <= MAX_SECONDS
        ):
            problems.append(f"session_hours: a number of hours over 0, not {hours!r}")
        else:
            session_seconds = hours * 3600.0

    providers: dict[str, ProviderConfig] = {}
    raw_providers = data.get("providers", {})
    if not isinstance(raw_providers, Mapping):
        problems.append("providers: a table of providers by id")
        raw_providers = {}
    for provider_id, table in raw_providers.items():
        provider = _provider(provider_id, table, environ, problems)
        if provider is not None:
            providers[provider_id] = provider

    allow: list[Allow] = []
    raw_allow = data.get("allow", [])
    if not isinstance(raw_allow, list):
        problems.append("allow: an array of [[allow]] tables")
        raw_allow = []
    for index, entry in enumerate(raw_allow, start=1):
        parsed = _allow(index, entry, raw_providers, problems)
        if parsed is not None:
            allow.append(parsed)

    if providers and not allow:
        problems.append("allow: no entry, so nobody could sign in")
    if problems:
        raise AuthConfigError(problems)
    return AuthConfig(
        public_url=public_url,
        session_seconds=session_seconds,
        providers=providers,
        allow=tuple(allow),
    )


def _provider(
    provider_id: str,
    table: object,
    environ: Mapping[str, str],
    problems: list[str],
) -> ProviderConfig | None:
    where = f"providers.{provider_id}"
    if not _PROVIDER_ID.fullmatch(provider_id):
        problems.append(
            f"{where}: an id is up to 40 lower-case letters, digits, _ and -"
        )
        return None
    if not isinstance(table, Mapping):
        problems.append(f"{where}: a table")
        return None
    before = len(problems)
    _unknown(
        table,
        {
            "title",
            "issuer",
            "client_id",
            "client_secret_env",
            "scopes",
            "groups_claim",
            "token_endpoint_auth",
        },
        where,
        problems,
    )
    title = _string(table, "title", where, problems, default=provider_id)
    client_id = _string(table, "client_id", where, problems)
    issuer = ""
    raw_issuer = _string(table, "issuer", where, problems)
    if raw_issuer:
        try:
            issuer = normalise_issuer(raw_issuer)
        except ValueError as exc:
            problems.append(f"{where}.issuer: {exc}")
    secret = ""
    variable = _string(table, "client_secret_env", where, problems)
    if variable:
        secret = environ.get(variable, "")
        if not secret:
            problems.append(f"{where}.client_secret_env: ${variable} is not set")
    scopes = DEFAULT_SCOPES
    if "scopes" in table:
        raw = table["scopes"]
        if not (isinstance(raw, list) and all(isinstance(s, str) and s for s in raw)):
            problems.append(f"{where}.scopes: an array of scope names")
        elif "openid" not in raw:
            problems.append(f"{where}.scopes: must include 'openid'")
        else:
            scopes = tuple(raw)
    groups_claim = None
    if "groups_claim" in table:
        groups_claim = _string(table, "groups_claim", where, problems) or None
    auth = "client_secret_basic"
    if "token_endpoint_auth" in table:
        auth = _string(table, "token_endpoint_auth", where, problems)
        if auth and auth not in TOKEN_ENDPOINT_AUTH:
            problems.append(
                f"{where}.token_endpoint_auth: one of {', '.join(TOKEN_ENDPOINT_AUTH)}"
            )
    if len(problems) > before:
        return None
    return ProviderConfig(
        id=provider_id,
        title=title,
        issuer=issuer,
        client_id=client_id,
        client_secret=secret,
        scopes=scopes,
        groups_claim=groups_claim,
        token_endpoint_auth=auth,
    )


_MATCHERS = {matcher.value: matcher for matcher in Matcher}


def _allow(
    index: int,
    entry: object,
    declared: Mapping[str, object],
    problems: list[str],
) -> Allow | None:
    where = f"allow {index}"
    if not isinstance(entry, Mapping):
        problems.append(f"{where}: a table")
        return None
    _unknown(entry, {"provider", *_MATCHERS}, where, problems)
    provider_id = entry.get("provider")
    if not isinstance(provider_id, str) or provider_id not in declared:
        problems.append(f"{where}: provider {provider_id!r} is not one of [providers]")
        return None
    given = [name for name in _MATCHERS if name in entry]
    if len(given) != 1:
        problems.append(
            f"{where}: exactly one of {', '.join(_MATCHERS)}, not "
            + (", ".join(given) or "none")
        )
        return None
    (name,) = given
    matcher = _MATCHERS[name]
    raw = entry[name]
    value: str | None
    if matcher is Matcher.EVERYONE:
        if raw is not True:
            problems.append(f"{where}: everyone = true, or no everyone at all")
            return None
        value = None
    elif not isinstance(raw, str) or not raw:
        problems.append(f"{where}: {name} is a non-empty string")
        return None
    else:
        value = raw
    # From the provider's table, so the rule is checked even when the
    # provider has problems of its own.
    table = declared[provider_id]
    issuer = table.get("issuer") if isinstance(table, Mapping) else None
    if matcher is Matcher.EMAIL_DOMAIN and _is_google(issuer):
        problems.append(
            f"{where}: email_domain is refused for Google, which verifies personal "
            "accounts registered with any address: use hosted_domain for a "
            "Workspace, or email for one person"
        )
        return None
    if matcher is Matcher.HOSTED_DOMAIN and not _is_google(issuer):
        problems.append(
            f"{where}: hosted_domain is Google's hd claim, and means nothing from "
            f"another provider: use group or email for {provider_id!r}"
        )
        return None
    return Allow(provider_id, matcher, value)


def _is_google(issuer: object) -> bool:
    try:
        return isinstance(issuer, str) and normalise_issuer(issuer) == GOOGLE_ISSUER
    except ValueError:
        return False


def _unknown(
    table: Mapping[str, object], known: set[str], where: str, problems: list[str]
) -> None:
    for key in table:
        if key not in known:
            prefix = f"{where}: " if where else ""
            problems.append(f"{prefix}unknown key {key!r}")


def _string(
    table: Mapping[str, object],
    key: str,
    where: str,
    problems: list[str],
    *,
    default: str | None = None,
) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or not value:
        problems.append(f"{where}.{key}: missing, or not a non-empty string")
        return ""
    return value


def normalise_origin(url: str) -> str:
    """``url`` as a browser's ``Origin``; ``ValueError`` unless it is an origin.

    ``http`` only for a loopback host: nothing else would keep a cookie or a
    token from the network between.
    """
    scheme, host, port, path = _split(url)
    if path not in ("", "/"):
        raise ValueError(
            f"{url!r} has a path: the manager is served at the root of its origin"
        )
    return _joined(scheme, host, port)


def normalise_issuer(url: str) -> str:
    """An issuer in one form: its path kept, without a trailing slash."""
    scheme, host, port, path = _split(url)
    return _joined(scheme, host, port) + path.rstrip("/")


def _split(url: str) -> tuple[str, str, int | None, str]:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise ValueError(f"{url!r} is not an http or https URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError(f"{url!r} holds more than a scheme, host, port and path")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError(f"{url!r} has no host")
    try:
        port = parts.port
    except ValueError:
        raise ValueError(f"{url!r} has a port that is not one") from None
    if scheme == "http" and not is_loopback(host):
        raise ValueError(f"{url!r} is http on a host that is not loopback: use https")
    return scheme, host, port, parts.path


def _joined(scheme: str, host: str, port: int | None) -> str:
    shown = f"[{host}]" if ":" in host else host
    if port is None or port == _DEFAULT_PORTS[scheme]:
        return f"{scheme}://{shown}"
    return f"{scheme}://{shown}:{port}"


def is_loopback(host: str) -> bool:
    """Whether ``host`` names this machine: ``localhost``, or a loopback address."""
    host = host.strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
