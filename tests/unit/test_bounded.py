"""Bounded, JSON-serialisable copies of untrusted values.

Error envelopes carry details that can originate on a device. These tests check the two
properties every copy must have whatever the input: it is bounded, and so is the work of
making it, which is observed by counting what was read rather than by timing; and it always
serialises.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from enum import IntEnum, StrEnum

import pydantic_core
import pytest

from sphereloom.domain.bounded import (
    ELLIPSIS,
    MAX_DEPTH,
    MAX_ITEMS,
    MAX_NODES,
    UNREPRESENTABLE,
    JsonValue,
    bounded_json,
    bounded_text,
)


class _CountingList(list[object]):
    """A list that counts how many of its items have been read."""

    pulled = 0

    def __iter__(self) -> Iterator[object]:
        for item in super().__iter__():
            type(self).pulled += 1
            yield item


class _CountingDict(dict[str, object]):
    """A dict that counts how many of its entries have been read."""

    pulled = 0

    def items(self) -> Iterator[tuple[str, object]]:  # type: ignore[override]  # a generator is enough to count reads
        for entry in super().items():
            type(self).pulled += 1
            yield entry


class _Colour(StrEnum):
    RED = "red"


class _Level(IntEnum):
    HIGH = 3


def _serialises(value: JsonValue) -> None:
    json.dumps(value, allow_nan=False).encode("utf-8")
    pydantic_core.to_json(value)


def _only_builtin_types(value: JsonValue) -> bool:
    if isinstance(value, dict):
        return type(value) is dict and all(
            type(key) is str and _only_builtin_types(item) for key, item in value.items()
        )
    if isinstance(value, list):
        return type(value) is list and all(_only_builtin_types(item) for item in value)
    return value is None or type(value) in {str, int, float, bool}


def _deeply_nested(depth: int) -> list[object]:
    root: list[object] = []
    current = root
    for _ in range(depth):
        child: list[object] = []
        current.append(child)
        current = child
    return root


def _cyclic() -> dict[str, object]:
    cycle: dict[str, object] = {}
    cycle["self"] = cycle
    return cycle


def test_a_long_string_is_cut_and_marked() -> None:
    assert bounded_text("a" * 1000, 10) == "a" * 10 + ELLIPSIS


def test_a_string_at_the_limit_is_kept_whole() -> None:
    assert bounded_text("a" * 10, 10) == "a" * 10


def test_a_lone_surrogate_is_replaced() -> None:
    assert bounded_text("ok\ud800") == "ok?"


def test_a_list_is_read_no_further_than_its_item_limit() -> None:
    _CountingList.pulled = 0
    value = _CountingList(range(10_000))

    copy = bounded_json(value)

    assert copy == [*range(MAX_ITEMS), ELLIPSIS]
    assert _CountingList.pulled == MAX_ITEMS + 1


def test_an_object_is_read_no_further_than_its_item_limit() -> None:
    _CountingDict.pulled = 0
    value = _CountingDict({f"k{index}": index for index in range(10_000)})

    copy = bounded_json(value)

    assert isinstance(copy, dict)
    assert len(copy) == MAX_ITEMS + 1
    assert copy[ELLIPSIS] == f"{10_000 - MAX_ITEMS} more"
    assert _CountingDict.pulled == MAX_ITEMS + 1


def test_the_node_budget_caps_a_broad_tree() -> None:
    """Per-level limits multiply: twenty lists of twenty items is already 420 nodes."""
    _CountingList.pulled = 0
    shared = _CountingList(range(MAX_ITEMS))

    bounded_json([shared] * MAX_ITEMS)

    assert _CountingList.pulled <= MAX_NODES


def test_nesting_past_the_depth_limit_is_cut() -> None:
    copy = bounded_json(_deeply_nested(10))

    for _ in range(MAX_DEPTH - 1):
        assert isinstance(copy, list)
        copy = copy[0]
    assert copy == [ELLIPSIS]


def test_a_huge_key_is_bounded() -> None:
    copy = bounded_json({"k" * 10_000: 1})

    assert isinstance(copy, dict)
    assert list(copy) == ["k" * 200 + ELLIPSIS]


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        10**5000,
        "\ud800",
        b"bytes",
        {1, 2},
        object(),
        {1: "integer key", (1, 2): "tuple key"},
        _cyclic(),
        _deeply_nested(10_000),
        {"nested": [float("-inf"), {"x": 10**5000, "y": "\udfff"}]},
    ],
    ids=[
        "nan",
        "infinity",
        "huge-integer",
        "lone-surrogate",
        "bytes",
        "set",
        "object",
        "non-string-keys",
        "cycle",
        "deep",
        "mixed",
    ],
)
def test_any_value_becomes_serialisable(value: object) -> None:
    copy = bounded_json(value)

    _serialises(copy)
    assert _only_builtin_types(copy)


def test_unrepresentable_values_are_marked() -> None:
    assert bounded_json([float("nan"), 10**5000, b"x"]) == [UNREPRESENTABLE] * 3


def test_subclasses_become_plain_values() -> None:
    copy = bounded_json({"colour": _Colour.RED, "level": _Level.HIGH, "flag": True})

    assert copy == {"colour": "red", "level": 3, "flag": True}
    assert _only_builtin_types(copy)
