# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The manager service. Install with ``pip install neorc[manager]``."""

from neorc.manager._app import create_app
from neorc.manager._service import build_app, run

__all__ = ["build_app", "create_app", "run"]
