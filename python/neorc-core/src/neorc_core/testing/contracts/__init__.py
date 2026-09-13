# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Test suites every implementation of a port must pass.

A suite is a class of tests written against the port alone. To run it on an
adapter, subclass it in a test module, under a name pytest collects, and
provide the fixture the suite names::

    class TestMyTaskStore(TaskStoreContract):
        @pytest.fixture
        def store(self, my_store: MyTaskStore) -> TaskStore:
            return my_store

The fixture should hand over an adapter with nothing in it. The tests are
coroutines, so the suite expects pytest-asyncio in ``auto`` mode. Call
``pytest.register_assert_rewrite("neorc_core.testing.contracts")`` in a
``conftest.py`` before importing a suite, for pytest's detailed assertion
messages.
"""

from neorc_core.testing.contracts._queue_client import QueueClientContract
from neorc_core.testing.contracts._task_store import TaskStoreContract

__all__ = ["QueueClientContract", "TaskStoreContract"]
