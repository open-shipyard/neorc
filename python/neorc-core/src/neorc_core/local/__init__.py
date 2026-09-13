# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""In-memory adapters: the whole system in one process.

Nothing here survives a restart or is seen by another process. They are not
simplified fakes: they keep the semantics of the deployed adapters, and pass
the same contract suites in ``neorc_core.testing.contracts``.
"""

from neorc_core.local._memory_store import MemoryStore
from neorc_core.local._notifier import MemoryTaskNotifier
from neorc_core.local._queue_client import DirectQueueClient
from neorc_core.local._store import MemoryTaskStore

__all__ = ["DirectQueueClient", "MemoryStore", "MemoryTaskNotifier", "MemoryTaskStore"]
