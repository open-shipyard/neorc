# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The Postgres credential store, against a real server, held to the contract."""

from __future__ import annotations

import pytest

from neorc.postgres import PostgresCredentialStore
from neorc_core import CredentialStore
from neorc_core.testing.contracts import CredentialStoreContract

pytestmark = pytest.mark.postgres


class TestPostgresCredentialStore(CredentialStoreContract):
    @pytest.fixture
    def credentials(self, pg_credentials: PostgresCredentialStore) -> CredentialStore:
        return pg_credentials
