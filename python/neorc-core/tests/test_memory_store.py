# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The in-memory store for flows, held to the store contract."""

import pytest

from neorc_core import Store
from neorc_core.local import MemoryStore
from neorc_core.testing.contracts import StoreContract


class TestMemoryStore(StoreContract):
    @pytest.fixture
    def store(self) -> Store:
        return MemoryStore()
