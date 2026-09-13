# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The Postgres store, against a real server, held to the store contract.

The contract's concurrent claimers mean something here that they cannot in
memory: real connections, where only ``FOR UPDATE SKIP LOCKED`` keeps two of
them from sharing a task.
"""

from __future__ import annotations

import pytest

from neorc.postgres import PostgresTaskStore
from neorc_core import TaskStore
from neorc_core.testing.contracts import TaskStoreContract

pytestmark = pytest.mark.postgres


class TestPostgresTaskStore(TaskStoreContract):
    @pytest.fixture
    def store(self, pg_store: PostgresTaskStore) -> TaskStore:
        return pg_store
