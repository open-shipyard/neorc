# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The in-memory credential store, held to the credential store contract."""

import pytest

from neorc_core import CredentialStore
from neorc_core.local import MemoryCredentialStore
from neorc_core.testing.contracts import CredentialStoreContract


class TestMemoryCredentialStore(CredentialStoreContract):
    @pytest.fixture
    def credentials(self) -> CredentialStore:
        return MemoryCredentialStore()
