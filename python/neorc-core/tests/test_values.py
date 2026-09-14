# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Encoding the values that travel between tasks: JSON plus tagged datetimes."""

import json
import time
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from neorc_core import InvalidValueError, PayloadTooLargeError
from neorc_core._values import (
    MAX_JSON_DEPTH,
    MAX_PAYLOAD_BYTES,
    JsonValue,
    decode,
    dumps,
    encode,
    ensure_fits,
    loads,
)

WHEN = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)


def test_json_values_encode_unchanged() -> None:
    value: JsonValue = {"a": [1, 2.5, "x", True, None], "b": {"c": False}}

    assert encode(value) == value
    assert decode(value) == value


def test_datetimes_are_tagged_at_any_depth() -> None:
    encoded = encode({"when": WHEN, "all": [WHEN]})

    tag = {"$datetime": "2026-09-13T10:00:00+00:00"}
    assert encoded == {"when": tag, "all": [tag]}
    assert decode(encoded) == {"when": WHEN, "all": [WHEN]}


def test_round_trip_through_json_keeps_the_timezone() -> None:
    offset = datetime(2026, 1, 1, 8, 30, tzinfo=timezone(timedelta(hours=-3)))

    assert loads(dumps({"when": offset})) == {"when": offset}


@pytest.mark.parametrize(
    "value",
    [
        {1, 2},
        (1, 2),
        date(2026, 9, 13),
        datetime(2026, 9, 13),
        float("nan"),
        float("inf"),
        {1: "non-string key"},
        {"$reserved": 1},
        {"nested": [{"$datetime": "2026-09-13T10:00:00+00:00", "other": 1}]},
        object(),
    ],
    ids=[
        "set",
        "tuple",
        "date",
        "naive-datetime",
        "nan",
        "infinity",
        "non-string-key",
        "reserved-key",
        "tag-with-extra-key",
        "object",
    ],
)
def test_unsupported_values_are_rejected_when_encoding(value: object) -> None:
    with pytest.raises(InvalidValueError):
        encode(value)


@pytest.mark.parametrize(
    "value",
    [
        {"$datetime": 34},
        {"$datetime": "not a date"},
        {"$datetime": "2026-09-13T10:00:00"},
        {"$other": 1},
        [{"$datetime": "2026-09-13T10:00:00+00:00", "extra": 1}],
    ],
    ids=["number", "not-iso", "naive", "reserved-key", "tag-with-extra-key"],
)
def test_malformed_values_are_rejected_when_decoding(value: object) -> None:
    with pytest.raises(InvalidValueError):
        decode(value)  # type: ignore[arg-type]


def test_invalid_value_is_a_value_error() -> None:
    with pytest.raises(ValueError):
        encode({1, 2})


@pytest.mark.parametrize(
    "value",
    [
        "a\x00b",
        {"key\x00": 1},
        {"nested": ["\x00"]},
        "\ud800",
        {"\udfff": 1},
    ],
    ids=["nul", "nul-in-key", "nul-nested", "lone-surrogate", "surrogate-key"],
)
def test_text_no_store_can_hold_is_rejected_either_way(value: JsonValue) -> None:
    """NUL and lone surrogates: Postgres text cannot store them, so nobody may."""
    with pytest.raises(InvalidValueError):
        encode(value)
    with pytest.raises(InvalidValueError):
        decode(value)


def test_nul_in_json_text_is_rejected_when_loading() -> None:
    with pytest.raises(InvalidValueError, match="NUL"):
        loads('{"a": "x\\u0000y"}')


@pytest.mark.parametrize("text", ["NaN", "[Infinity]", '{"a": -Infinity}'])
def test_numbers_json_text_cannot_carry_back_are_rejected_when_decoding(
    text: str,
) -> None:
    """json.loads takes them; dumps_json, and every store, would not."""
    with pytest.raises(InvalidValueError, match="not a JSON number"):
        loads(text)
    with pytest.raises(InvalidValueError, match="not a JSON number"):
        decode(json.loads(text))


def test_the_error_names_the_path_and_holds_no_nul_itself() -> None:
    with pytest.raises(InvalidValueError, match=r"^input 'x'\.a\[1\]: text holds NUL"):
        encode({"a": ["fine", "bad\x00"]}, path="input 'x'")
    with pytest.raises(InvalidValueError) as caught:
        decode({"k\x00": 1})
    assert "\x00" not in str(caught.value)


def test_size_limit_counts_utf8_bytes() -> None:
    fits = "x" * (MAX_PAYLOAD_BYTES - 2)
    ensure_fits(dumps(fits))

    with pytest.raises(PayloadTooLargeError):
        ensure_fits(dumps("é" * (MAX_PAYLOAD_BYTES // 2)))


def test_json_at_the_depth_limit_parses() -> None:
    text = "[" * MAX_JSON_DEPTH + "]" * MAX_JSON_DEPTH

    assert loads(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "[" * (MAX_JSON_DEPTH + 1) + "]" * (MAX_JSON_DEPTH + 1),
        '{"a":' * (MAX_JSON_DEPTH + 1) + "1" + "}" * (MAX_JSON_DEPTH + 1),
        # Far deeper than any parser's stack, which must never be reached.
        "[" * 100_000 + "]" * 100_000,
    ],
    ids=["one-too-deep", "objects", "stack-deep"],
)
def test_json_nested_too_deep_is_rejected_before_parsing(text: str) -> None:
    with pytest.raises(InvalidValueError, match="deeper than"):
        loads(text)


def test_brackets_inside_strings_do_not_count_as_nesting() -> None:
    # Escaped quotes and backslashes must not end a string early either.
    value = {"s": '[{"\\' * MAX_JSON_DEPTH, "t": ["\\", '"']}

    assert loads(json.dumps(value)) == value


def test_an_unterminated_string_is_scanned_in_linear_time() -> None:
    """Escaped quotes after an open one once made the depth scan quadratic."""
    text = '"' + '\\"' * 500_000
    started = time.monotonic()

    with pytest.raises(ValueError):
        loads(text)

    assert time.monotonic() - started < 2


def test_text_that_is_not_utf_8_does_not_fit() -> None:
    with pytest.raises(InvalidValueError, match="not valid UTF-8"):
        ensure_fits('"\ud800"')
