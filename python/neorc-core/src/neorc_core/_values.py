# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The values that travel between tasks, and how they are written down.

Flow inputs, task inputs and task results are JSON values plus datetimes. On the
wire and in storage a datetime is an object with a single ``$datetime`` key; user
code only ever sees ``datetime`` objects. See docs/specs/workers-and-manager.md,
"Values".
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

from neorc_core._errors import InvalidValueError, PayloadTooLargeError

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
"""A value as JSON holds it: datetimes are already tagged."""

MAX_PAYLOAD_BYTES = 1024 * 1024
"""The largest encoded payload, in bytes of UTF-8: the SQS message limit."""

DATETIME_TAG = "$datetime"
RESERVED_PREFIX = "$"


def encode(value: Any) -> JsonValue:
    """Turn a user value into its JSON form, tagging datetimes.

    Raises ``InvalidValueError`` for anything neorc does not carry: sets,
    tuples, dates, naive datetimes, non-finite floats, non-string keys and keys
    starting with ``$``.
    """
    return _encode(value, "value")


def decode(value: JsonValue) -> Any:
    """Turn a JSON form back into a user value, untagging datetimes.

    Raises ``InvalidValueError`` for a malformed tag or any other ``$`` key.
    """
    return _decode(value, "value")


def dumps(value: Any) -> str:
    """Encode a user value and serialise it to compact JSON."""
    return json.dumps(encode(value), separators=(",", ":"), ensure_ascii=False)


def loads(text: str) -> Any:
    """Parse JSON and decode it into a user value."""
    return decode(json.loads(text))


def ensure_fits(encoded: str, *, limit: int = MAX_PAYLOAD_BYTES) -> None:
    """Raise ``PayloadTooLargeError`` if ``encoded`` is over ``limit`` bytes."""
    size = len(encoded.encode("utf-8"))
    if size > limit:
        raise PayloadTooLargeError(f"payload is {size} bytes, over the {limit} limit")


def _encode(value: Any, path: str) -> JsonValue:
    # bool before int: a bool is an int in Python.
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidValueError(f"{path}: {value} is not a JSON number")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidValueError(f"{path}: datetime {value} has no timezone")
        return {DATETIME_TAG: value.isoformat()}
    if isinstance(value, list):
        return [_encode(item, f"{path}[{i}]") for i, item in enumerate(value)]
    if isinstance(value, dict):
        encoded: dict[str, JsonValue] = {}
        for key, item in value.items():
            _check_key(key, path)
            encoded[key] = _encode(item, f"{path}.{key}")
        return encoded
    raise InvalidValueError(f"{path}: {type(value).__name__} is not a supported type")


def _decode(value: JsonValue, path: str) -> Any:
    if isinstance(value, list):
        return [_decode(item, f"{path}[{i}]") for i, item in enumerate(value)]
    if isinstance(value, dict):
        if DATETIME_TAG in value and len(value) == 1:
            return _parse_datetime(value[DATETIME_TAG], path)
        decoded: dict[str, Any] = {}
        for key, item in value.items():
            _check_key(key, path)
            decoded[key] = _decode(item, f"{path}.{key}")
        return decoded
    return value


def _check_key(key: object, path: str) -> None:
    if not isinstance(key, str):
        raise InvalidValueError(f"{path}: key {key!r} is not a string")
    if key.startswith(RESERVED_PREFIX):
        raise InvalidValueError(f"{path}: key {key!r} is reserved for neorc")


def _parse_datetime(text: JsonValue, path: str) -> datetime:
    if not isinstance(text, str):
        raise InvalidValueError(f"{path}: {DATETIME_TAG} must hold an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise InvalidValueError(
            f"{path}: {text!r} is not an ISO 8601 datetime"
        ) from None
    if parsed.tzinfo is None:
        raise InvalidValueError(f"{path}: datetime {text!r} has no timezone")
    return parsed
