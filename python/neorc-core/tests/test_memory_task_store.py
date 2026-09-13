# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The in-memory task store, held to the store contract."""

import pytest

from neorc_core import TaskStore
from neorc_core.local import MemoryTaskStore
from neorc_core.testing.contracts import TaskStoreContract


class TestMemoryTaskStore(TaskStoreContract):
    @pytest.fixture
    def store(self) -> TaskStore:
        return MemoryTaskStore()
