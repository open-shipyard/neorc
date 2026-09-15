# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Signing people in with OpenID Connect providers, from ``auth.toml``."""

from neorc.auth._config import (
    AuthConfig,
    AuthConfigError,
    ProviderConfig,
    load_auth_config,
    parse_auth_config,
)
from neorc.auth._oidc import OidcProvider, SignInFailed

__all__ = [
    "AuthConfig",
    "AuthConfigError",
    "OidcProvider",
    "ProviderConfig",
    "SignInFailed",
    "load_auth_config",
    "parse_auth_config",
]
