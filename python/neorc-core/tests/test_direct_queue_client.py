# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The direct queue client on the in-memory adapters, held to the client contract."""

import pytest

from neorc_core import Manager, QueueClient
from neorc_core.local import DirectQueueClient
from neorc_core.testing.contracts import QueueClientContract


class TestDirectQueueClient(QueueClientContract):
    @pytest.fixture
    def queue_client(self, manager: Manager) -> QueueClient:
        return DirectQueueClient(manager)
