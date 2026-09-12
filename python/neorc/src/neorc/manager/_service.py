# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Assembling and running the manager process."""

from __future__ import annotations

from fastapi import FastAPI

# A manager serves workers on other hosts, so it binds every interface.
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8420


def build_app() -> FastAPI:
    """Build the application from the environment: store, notifier, then routes.

    Which adapter backs the store is a deployment choice; nothing above this
    function names Postgres.
    """
    raise NotImplementedError


def run(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Serve the manager until interrupted. Blocks."""
    raise NotImplementedError
