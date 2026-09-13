# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Run a throwaway Postgres for the example and print its URL. Ctrl+C stops it.

Uses ``pgserver`` from the dev dependencies, so no Docker or system install.
"""

from __future__ import annotations

import signal
import tempfile

import pgserver

with tempfile.TemporaryDirectory(prefix="neorc-pgdata-") as data_dir:
    server = pgserver.get_server(data_dir, cleanup_mode="stop")
    try:
        print(f"export NEORC_DATABASE_URL='{server.get_uri()}'", flush=True)
        signal.pause()
    except KeyboardInterrupt:
        pass
    finally:
        server.cleanup()
