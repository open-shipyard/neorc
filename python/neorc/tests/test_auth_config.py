# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""``auth.toml`` read into providers and an allow list, every problem at once."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from neorc.auth import AuthConfigError, load_auth_config, parse_auth_config
from neorc.auth._config import normalise_issuer, normalise_origin
from neorc_core import Allow, Matcher

SECRETS = {"GOOGLE_SECRET": "g-secret", "OKTA_SECRET": "o-secret"}

FULL = """
public_url = "HTTPS://Neorc.Example.com:443/"
session_hours = 8

[providers.google]
title = "Google"
issuer = "https://accounts.google.com/"
client_id = "1234.apps.googleusercontent.com"
client_secret_env = "GOOGLE_SECRET"

[providers.okta]
title = "Okta"
issuer = "https://Example.okta.com/oauth2/default/"
client_id = "0oa1"
client_secret_env = "OKTA_SECRET"
groups_claim = "groups"
scopes = ["openid", "email", "profile", "groups"]
token_endpoint_auth = "client_secret_post"

[[allow]]
provider = "google"
hosted_domain = "example.com"

[[allow]]
provider = "google"
email = "ada@gmail.com"

[[allow]]
provider = "okta"
group = "neorc-users"

[[allow]]
provider = "okta"
everyone = true
"""


def parsed(text: str, environ: dict[str, str] | None = None) -> Any:
    return parse_auth_config(
        tomllib.loads(text), SECRETS if environ is None else environ
    )


def problems(text: str, environ: dict[str, str] | None = None) -> list[str]:
    with pytest.raises(AuthConfigError) as refused:
        parsed(text, environ)
    return refused.value.problems


def test_a_whole_config_reads_with_its_urls_in_one_form() -> None:
    config = parsed(FULL)

    assert config.public_url == "https://neorc.example.com"
    assert config.secure
    assert config.session_seconds == 8 * 3600
    assert config.redirect_uri("okta") == (
        "https://neorc.example.com/auth/callback/okta"
    )
    google, okta = config.providers["google"], config.providers["okta"]
    assert google.issuer == "https://accounts.google.com" and google.is_google
    assert google.client_secret == "g-secret"
    assert google.scopes == ("openid", "email", "profile")
    assert google.token_endpoint_auth == "client_secret_basic"
    assert okta.issuer == "https://example.okta.com/oauth2/default"
    assert not okta.is_google
    assert okta.groups_claim == "groups"
    assert okta.scopes == ("openid", "email", "profile", "groups")
    assert okta.token_endpoint_auth == "client_secret_post"
    assert config.allow == (
        Allow("google", Matcher.HOSTED_DOMAIN, "example.com"),
        Allow("google", Matcher.EMAIL, "ada@gmail.com"),
        Allow("okta", Matcher.GROUP, "neorc-users"),
        Allow("okta", Matcher.EVERYONE),
    )


def test_a_client_secret_is_never_in_a_repr() -> None:
    config = parsed(FULL)

    assert "g-secret" not in repr(config)
    assert "o-secret" not in repr(config.providers)


def test_a_config_is_read_from_a_file(tmp_path: Path) -> None:
    path = tmp_path / "auth.toml"
    path.write_text(FULL)

    assert load_auth_config(path, SECRETS).providers.keys() == {"google", "okta"}
    path.write_text("public_url = ")
    with pytest.raises(AuthConfigError, match="not TOML"):
        load_auth_config(path, SECRETS)


def test_a_config_without_providers_is_tokens_and_a_public_url() -> None:
    config = parsed('public_url = "http://127.0.0.1:8420"')

    assert (config.providers, config.allow, config.secure) == ({}, (), False)


def test_every_problem_is_listed_at_once() -> None:
    found = problems(
        """
        public_url = "https://neorc.example.com/neorc"
        session_hours = 0
        colour = "blue"

        [providers.Google]
        issuer = "https://accounts.google.com"

        [providers.okta]
        issuer = "http://example.okta.com"
        client_id = "0oa1"
        client_secret_env = "MISSING_SECRET"
        scopes = ["email"]
        token_endpoint_auth = "private_key_jwt"
        shape = "round"

        [[allow]]
        provider = "okta"
        email = "a@example.com"
        group = "g"

        [[allow]]
        provider = "github"
        everyone = true

        [[allow]]
        provider = "okta"
        everyone = "yes"
        """,
    )

    expected = [
        "public_url: 'https://neorc.example.com/neorc' has a path",
        "session_hours: a number of hours over 0",
        "unknown key 'colour'",
        "providers.Google: an id is up to 40 lower-case letters",
        "providers.okta: unknown key 'shape'",
        "providers.okta.issuer: 'http://example.okta.com' is http on a host that "
        "is not loopback",
        "providers.okta.client_secret_env: $MISSING_SECRET is not set",
        "providers.okta.scopes: must include 'openid'",
        "providers.okta.token_endpoint_auth: one of",
        "allow 1: exactly one of",
        "allow 2: provider 'github' is not one of [providers]",
        "allow 3: everyone = true",
    ]
    for start in expected:
        assert any(p.startswith(start) for p in found), (start, found)


def test_a_provider_with_no_allow_entry_lets_nobody_in() -> None:
    text = FULL.split("[[allow]]")[0]

    assert problems(text) == ["allow: no entry, so nobody could sign in"]


@pytest.mark.parametrize(
    "issuer", ["https://accounts.google.com", "https://ACCOUNTS.google.com/"]
)
def test_an_email_domain_entry_is_refused_for_google(issuer: str) -> None:
    text = FULL.replace("https://accounts.google.com/", issuer).replace(
        'hosted_domain = "example.com"', 'email_domain = "example.com"'
    )

    (problem,) = problems(text)
    assert problem.startswith("allow 1: email_domain is refused for Google")


def test_the_google_rule_holds_while_the_provider_has_problems_of_its_own() -> None:
    text = FULL.replace('hosted_domain = "example.com"', 'email_domain = "gmail.com"')

    found = problems(text, {"OKTA_SECRET": "o"})

    assert any("GOOGLE_SECRET is not set" in p for p in found)
    assert any("email_domain is refused for Google" in p for p in found)


def test_a_hosted_domain_entry_is_refused_for_a_provider_other_than_google() -> None:
    text = FULL.replace('group = "neorc-users"', 'hosted_domain = "example.com"')

    (problem,) = problems(text)
    assert problem.startswith("allow 3: hosted_domain is Google's hd claim")


def test_an_email_domain_entry_is_taken_for_another_provider() -> None:
    config = parsed(
        FULL.replace('group = "neorc-users"', 'email_domain = "example.com"')
    )

    assert Allow("okta", Matcher.EMAIL_DOMAIN, "example.com") in config.allow


@pytest.mark.parametrize(
    ("url", "origin"),
    [
        ("https://neorc.example.com", "https://neorc.example.com"),
        ("https://NEORC.example.com/", "https://neorc.example.com"),
        ("https://neorc.example.com:443", "https://neorc.example.com"),
        ("https://neorc.example.com:8443", "https://neorc.example.com:8443"),
        ("http://127.0.0.1:8420", "http://127.0.0.1:8420"),
        ("http://localhost:80/", "http://localhost"),
        ("http://[::1]:8420", "http://[::1]:8420"),
    ],
)
def test_an_origin_has_one_form(url: str, origin: str) -> None:
    assert normalise_origin(url) == origin


@pytest.mark.parametrize(
    ("url", "problem"),
    [
        ("ftp://neorc.example.com", "not an http or https URL"),
        ("neorc.example.com", "not an http or https URL"),
        ("https://user:pw@neorc.example.com", "more than a scheme"),
        ("https://neorc.example.com/?x=1", "more than a scheme"),
        ("https://", "no host"),
        ("https://neorc.example.com:99999", "port that is not one"),
        ("http://neorc.example.com", "not loopback"),
        ("http://10.0.0.5:8420", "not loopback"),
        ("https://neorc.example.com/prefix", "has a path"),
    ],
)
def test_what_is_no_public_origin_is_refused(url: str, problem: str) -> None:
    with pytest.raises(ValueError, match=problem):
        normalise_origin(url)


def test_an_issuer_keeps_its_path_without_a_trailing_slash() -> None:
    assert (
        normalise_issuer("HTTPS://Example.okta.com:443/oauth2/default/")
        == "https://example.okta.com/oauth2/default"
    )
