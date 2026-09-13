# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Core primitives for the neorc orchestration system."""

from importlib.metadata import PackageNotFoundError, version

from neorc_core._errors import (
    ManagerUnavailableError,
    NeorcError,
    TaskNotFoundError,
    TaskStateError,
)
from neorc_core._manager import Manager
from neorc_core._task import (
    LEASED_STATUSES,
    TERMINAL_STATUSES,
    Payload,
    Task,
    TaskId,
    TaskStatus,
    ensure_transition,
)
from neorc_core._worker import TaskHandler, Worker
from neorc_core.ports import QueueClient, Subscription, TaskNotifier, TaskStore

try:
    __version__ = version("neorc-core")
except PackageNotFoundError:  # a source checkout on sys.path, not installed
    __version__ = "0+unknown"

__all__ = [
    "LEASED_STATUSES",
    "TERMINAL_STATUSES",
    "Manager",
    "ManagerUnavailableError",
    "NeorcError",
    "Payload",
    "QueueClient",
    "Subscription",
    "Task",
    "TaskHandler",
    "TaskId",
    "TaskNotFoundError",
    "TaskNotifier",
    "TaskStateError",
    "TaskStatus",
    "TaskStore",
    "Worker",
    "__version__",
    "ensure_transition",
]
