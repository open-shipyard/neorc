# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The exceptions neorc raises. Adapters translate their own failures to these."""


class NeorcError(Exception):
    """Base class for every error raised by neorc."""


class TaskNotFoundError(NeorcError):
    """No task exists with the given id."""


class TaskStateError(NeorcError):
    """A task was asked to make a transition its current status does not allow."""


class ManagerUnavailableError(NeorcError):
    """The manager service could not be reached, or answered with an error."""
