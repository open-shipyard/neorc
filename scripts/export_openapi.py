# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Write the manager's OpenAPI schema to the snapshot the UI build reads.

    uv run python scripts/export_openapi.py

The snapshot, ``ts/neorc-ui/openapi.json``, is committed; a test in ``neorc``
fails when it differs from the application's schema, so the UI's generated
types never drift from the routes, and the UI build needs no Python.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from neorc.manager import create_app
from neorc_core import Manager
from neorc_core.local import MemoryStore, MemoryTaskNotifier

SNAPSHOT = Path(__file__).resolve().parents[1] / "ts" / "neorc-ui" / "openapi.json"


def openapi_text() -> str:
    """The schema as the snapshot holds it: sorted keys, indented, one final newline."""
    app = create_app(
        Manager(MemoryStore(), tasks=MemoryTaskNotifier(), events=MemoryTaskNotifier())
    )
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(openapi_text())
    print(f"wrote {SNAPSHOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
