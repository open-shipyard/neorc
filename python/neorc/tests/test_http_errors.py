# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Every core error has a status, and crosses HTTP as itself."""

from __future__ import annotations

import pytest

from neorc._errors import STATUS_OF, WITH_PROBLEMS, error_body, error_from, status_of
from neorc_core import ManagerUnavailableError, NeorcError, PayloadTooLargeError


def _every_error(cls: type[NeorcError] = NeorcError) -> list[type[NeorcError]]:
    found = [cls]
    for subclass in cls.__subclasses__():
        found.extend(_every_error(subclass))
    return found


def test_every_core_error_has_a_status_of_its_own() -> None:
    """Adding an error to core means deciding what it answers with."""
    for cls in _every_error():
        assert cls in STATUS_OF, f"{cls.__name__} has no HTTP status"


@pytest.mark.parametrize("cls", _every_error(), ids=lambda cls: cls.__name__)
def test_every_core_error_round_trips_through_its_body(
    cls: type[NeorcError],
) -> None:
    exc = cls(["one", "two"]) if issubclass(cls, WITH_PROBLEMS) else cls("why")

    back = error_from(error_body(exc), status_of(exc))

    if status_of(exc) >= 500:
        # The manager could not serve, whatever it named: a caller retries.
        assert isinstance(back, ManagerUnavailableError)
        assert str(exc) in str(back)
        return
    assert type(back) is cls
    assert str(back) == str(exc)
    if isinstance(exc, WITH_PROBLEMS):
        assert getattr(back, "problems", None) == ["one", "two"]


def test_a_manager_that_could_not_serve_is_unavailable_whatever_it_names() -> None:
    """A run must not be failed for a request the manager never got to refuse."""
    body = {"error": "RunStateError", "detail": "the store is not open"}

    back = error_from(body, 500)

    assert isinstance(back, ManagerUnavailableError)
    assert "the store is not open" in str(back)


def test_a_subclass_answers_with_its_own_status_before_its_bases() -> None:
    assert status_of(PayloadTooLargeError("big")) == 413


@pytest.mark.parametrize(
    "body",
    [
        "not an object",
        {"detail": "no error field"},
        {"error": "ValueError", "detail": "not a neorc error"},
        {"error": "Manager", "detail": "a class, not an error"},
    ],
    ids=["text", "no-error-field", "not-a-neorc-error", "not-an-error"],
)
def test_a_body_naming_no_core_error_means_the_manager_is_unavailable(
    body: object,
) -> None:
    back = error_from(body, 500)

    assert isinstance(back, ManagerUnavailableError)
    assert "500" in str(back)
