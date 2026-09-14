# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Helpers for testing neorc and its adapters. Needs pytest and pytest-asyncio.

``contracts`` holds the suites every adapter must pass; ``examples`` the
scenarios that run the repository's examples through any ``ManagerClient``.
The in-memory adapters themselves live in ``neorc_core.local``.
"""
