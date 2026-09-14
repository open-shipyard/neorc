# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Core primitives for the neorc orchestration system."""

from importlib.metadata import PackageNotFoundError, version

from neorc_core._errors import (
    FlowDefinitionError,
    FlowNotFoundError,
    FlowVersionError,
    InvalidValueError,
    ManagerUnavailableError,
    NeorcError,
    PayloadTooLargeError,
    ResolutionError,
    RunNotFoundError,
    RunStateError,
    TaskNotFoundError,
    TaskStateError,
)
from neorc_core._flow_manager import FlowManager
from neorc_core._flow_worker import FlowWorker, HandlerError
from neorc_core._runs import (
    Event,
    EventKind,
    FlowTask,
    Run,
    RunId,
    RunStatus,
    StoredFlow,
    TaskDelivery,
)
from neorc_core._scheduler import Scheduler
from neorc_core._task import (
    LEASED_STATUSES,
    TERMINAL_STATUSES,
    TaskId,
    TaskStatus,
    ensure_transition,
)
from neorc_core.ports import (
    FlowQueueClient,
    ManagerClient,
    Store,
    Subscription,
    TaskNotifier,
)

try:
    __version__ = version("neorc-core")
except PackageNotFoundError:  # a source checkout on sys.path, not installed
    __version__ = "0+unknown"

__all__ = [
    "LEASED_STATUSES",
    "TERMINAL_STATUSES",
    "Event",
    "EventKind",
    "FlowDefinitionError",
    "FlowManager",
    "FlowNotFoundError",
    "FlowQueueClient",
    "FlowTask",
    "FlowVersionError",
    "FlowWorker",
    "HandlerError",
    "InvalidValueError",
    "ManagerClient",
    "ManagerUnavailableError",
    "NeorcError",
    "PayloadTooLargeError",
    "ResolutionError",
    "Run",
    "RunId",
    "RunNotFoundError",
    "RunStateError",
    "RunStatus",
    "Scheduler",
    "Store",
    "StoredFlow",
    "Subscription",
    "TaskDelivery",
    "TaskId",
    "TaskNotFoundError",
    "TaskNotifier",
    "TaskStateError",
    "TaskStatus",
    "__version__",
    "ensure_transition",
]
