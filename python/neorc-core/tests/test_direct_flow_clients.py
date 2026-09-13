# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The direct flow clients, on the in-memory adapters, held to the client contracts."""

import pytest

from neorc_core import FlowManager, FlowQueueClient, ManagerClient
from neorc_core.local import (
    DirectFlowQueueClient,
    DirectManagerClient,
    MemoryStore,
    MemoryTaskNotifier,
)
from neorc_core.testing.contracts import FlowQueueClientContract, ManagerClientContract


@pytest.fixture
def flow_manager() -> FlowManager:
    return FlowManager(
        MemoryStore(), tasks=MemoryTaskNotifier(), events=MemoryTaskNotifier()
    )


class _Direct:
    @pytest.fixture
    def manager_client(self, flow_manager: FlowManager) -> ManagerClient:
        return DirectManagerClient(flow_manager)

    @pytest.fixture
    def queue_client(self, flow_manager: FlowManager) -> FlowQueueClient:
        return DirectFlowQueueClient(flow_manager)


class TestDirectManagerClient(_Direct, ManagerClientContract):
    pass


class TestDirectFlowQueueClient(_Direct, FlowQueueClientContract):
    pass
