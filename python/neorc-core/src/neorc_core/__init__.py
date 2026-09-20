# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Core primitives for the neorc orchestration system."""

from importlib.metadata import PackageNotFoundError, version

from neorc_core._access import (
    Access,
    Allow,
    ApiToken,
    Identity,
    Matcher,
    PendingLogin,
    Principal,
    PrincipalKind,
    Role,
)
from neorc_core._errors import (
    AuthenticationError,
    CrossSiteRequestError,
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
    SignInRefusedError,
    TaskNotFoundError,
    TaskStateError,
    UnsupportedMediaTypeError,
)
from neorc_core._manager import Manager
from neorc_core._runs import (
    Event,
    EventKind,
    Run,
    RunId,
    RunStatus,
    StoredFlow,
    Task,
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
from neorc_core._worker import HandlerError, Worker
from neorc_core.ports import (
    CredentialStore,
    ManagerClient,
    QueueClient,
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
    "Access",
    "Allow",
    "ApiToken",
    "AuthenticationError",
    "CredentialStore",
    "CrossSiteRequestError",
    "Event",
    "EventKind",
    "FlowDefinitionError",
    "FlowNotFoundError",
    "FlowVersionError",
    "HandlerError",
    "Identity",
    "InvalidValueError",
    "Manager",
    "ManagerClient",
    "ManagerUnavailableError",
    "Matcher",
    "NeorcError",
    "PayloadTooLargeError",
    "PendingLogin",
    "Principal",
    "PrincipalKind",
    "QueueClient",
    "ResolutionError",
    "Role",
    "Run",
    "RunId",
    "RunNotFoundError",
    "RunStateError",
    "RunStatus",
    "Scheduler",
    "SignInRefusedError",
    "Store",
    "StoredFlow",
    "Subscription",
    "Task",
    "TaskDelivery",
    "TaskId",
    "TaskNotFoundError",
    "TaskNotifier",
    "TaskStateError",
    "TaskStatus",
    "UnsupportedMediaTypeError",
    "Worker",
    "__version__",
    "ensure_transition",
]
