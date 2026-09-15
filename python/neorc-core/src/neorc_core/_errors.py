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


class InvalidValueError(NeorcError, ValueError):
    """A value cannot travel between tasks: a type, key or datetime neorc rejects."""


class PayloadTooLargeError(InvalidValueError):
    """An encoded payload is over the size limit."""


class FlowDefinitionError(NeorcError):
    """A flow definition is invalid. ``problems`` lists every problem found."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("invalid flow definition:\n" + "\n".join(problems))


class ResolutionError(NeorcError):
    """A reference cannot be resolved: a fan-out's ``over`` value is not a list."""


class FlowNotFoundError(NeorcError):
    """No flow, or no version of a flow, exists with the given name."""


class FlowVersionError(NeorcError, ValueError):
    """An upload breaks the version rules, or a run names a version not the latest."""


class RunNotFoundError(NeorcError):
    """No run exists with the given id."""


class RunStateError(NeorcError):
    """A run cannot take the request: it is no longer active, or already finished."""


class AuthenticationError(NeorcError):
    """A request carries no identity, or one that is not accepted."""


class SignInRefusedError(NeorcError):
    """An identity provider vouched for a person whom no allow entry lets in."""
