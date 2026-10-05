"""Bounded, JSON-safe rendering of untrusted payloads.

This is the shared implementation behind error envelopes and captured fixtures. It existed
twice before, and only one copy was correct, which is why it now lives in one place.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from sphereloom.domain.payloads import (
    ELLIPSIS,
    MALFORMED,
    MAX_NODES,
    bounded_payload,
    bounded_text,
    strict_json_loads,
)


def test_a_short_string_survives_unchanged() -> None:
    assert bounded_text("a readable message") == "a readable message"


def test_a_long_string_is_truncated() -> None:
    assert len(bounded_text("x" * 10_000)) < 300


def test_whitespace_is_collapsed() -> None:
    """Multi-line vendor messages would otherwise sprawl through a single-line log."""
    assert bounded_text("two\n\nlines   here") == "two lines here"


@pytest.mark.parametrize("value", [42, 3.5, True, False])
def test_scalars_render(value: object) -> None:
    assert bounded_text(value) == str(value)


def test_none_renders_as_empty() -> None:
    assert bounded_text(None) == ""


@pytest.mark.parametrize("value", [["a"], {"k": "v"}, object()])
def test_a_structure_is_refused_rather_than_rendered(value: object) -> None:
    """`str()` on a payload materialises it all before truncating, defeating the bound."""
    assert bounded_text(value) == MALFORMED


# ---------------------------------------------------------------- payload bounds


def test_a_wide_object_is_bounded_by_item_count() -> None:
    result = bounded_payload({f"key{index}": index for index in range(1000)})

    assert isinstance(result, dict)
    assert len(result) <= 25


def test_a_long_list_is_bounded() -> None:
    result = bounded_payload(list(range(1000)))

    assert isinstance(result, list)
    assert len(result) <= 25


def test_deep_nesting_is_bounded() -> None:
    nested: dict[str, Any] = {}
    cursor = nested
    for _ in range(100):
        child: dict[str, Any] = {}
        cursor["deeper"] = child
        cursor = child

    assert len(str(bounded_payload(nested))) < 500


def test_a_broad_and_deep_payload_is_bounded_by_node_count() -> None:
    """Per-level limits multiply rather than add.

    Twenty items at four levels deep is a hundred and sixty thousand nodes, so a node
    budget is the only thing that actually bounds the result.
    """

    def branching(depth: int) -> Any:
        # Each level reuses one child object, so building this costs 120 entries while a
        # traversal still sees 20**6 nodes. Materialising it for real would be 64 million
        # leaves: the test would exhaust memory before the code under test ran.
        node: Any = "leaf"
        for _ in range(depth):
            child = node
            node = {f"key{index}": child for index in range(20)}
        return node

    assert len(str(bounded_payload(branching(6)))) < 10_000


def test_an_enormous_key_is_bounded() -> None:
    """Bounding values but not keys leaves one huge key able to flood the output alone."""
    result = bounded_payload({"x" * 50_000: "short"})

    assert len(str(result)) < 1000


# ---------------------------------------------------------------- JSON safety


def test_the_result_is_always_json_serialisable() -> None:
    """An error envelope that cannot be serialised turns a reported failure into a crash.

    That is the one thing the error path must never do.
    """
    hostile = {
        "an_object": object(),
        "a_set": {1, 2, 3},
        "nested": [object(), {"deeper": object()}],
    }

    json.dumps(bounded_payload(hostile))


def test_non_json_values_become_the_malformed_marker() -> None:
    result = bounded_payload({"value": object()})

    assert isinstance(result, dict)
    assert result["value"] == MALFORMED


def test_json_scalars_are_preserved_exactly() -> None:
    """Being strict must not destroy the data the excerpt exists to show."""
    result = bounded_payload({"n": 42, "f": 1.5, "b": True, "nil": None, "s": "text"})

    assert result == {"n": 42, "f": 1.5, "b": True, "nil": None, "s": "text"}


def test_a_tuple_is_rendered_as_a_list() -> None:
    assert bounded_payload(("a", "b")) == ["a", "b"]


def test_truncation_is_visible() -> None:
    """A silently shortened payload would mislead whoever is debugging it."""
    rendered = str(bounded_payload({f"key{index}": index for index in range(100)}))

    assert ELLIPSIS in rendered


def test_the_node_budget_is_respected_exactly() -> None:
    result = bounded_payload(list(range(MAX_NODES * 10)), max_items=MAX_NODES * 10)

    assert isinstance(result, list)
    assert len(result) <= MAX_NODES + 1


def test_bounding_is_idempotent() -> None:
    once = bounded_payload({"a": ["x" * 1000] * 50})

    assert bounded_payload(once) == once


def test_a_non_finite_float_is_replaced() -> None:
    """`json.dumps` writes NaN bare, so one device value would break the whole envelope."""
    result = bounded_payload({"a": float("nan"), "b": float("inf"), "c": float("-inf")})

    assert result == {"a": MALFORMED, "b": MALFORMED, "c": MALFORMED}
    json.dumps(result, allow_nan=False)


def test_an_enormous_integer_is_replaced() -> None:
    """CPython raises rather than render an integer wider than 4300 digits."""
    result = bounded_payload({"value": 10**5000})

    assert result == {"value": MALFORMED}
    json.dumps(result, allow_nan=False)


def test_an_ordinary_integer_survives() -> None:
    assert bounded_payload({"value": 10**100}) == {"value": 10**100}


@pytest.mark.parametrize(
    "payload",
    [
        {"a": float("nan")},
        {"a": 10**5000},
        [float("inf"), 10**5000],
        {"a": {"b": [float("nan"), {"c": 10**9000}]}},
    ],
)
def test_the_result_serialises_under_strict_json(payload: Any) -> None:
    """The contract is JSON-safe, which means strict JSON, not Python's lenient dialect."""
    json.dumps(bounded_payload(payload), allow_nan=False)


def test_an_integer_is_truncated_like_any_other_scalar() -> None:
    """The limit was a no-op for numbers, so a long integer ignored its bound."""
    # Representable but 201 digits long, so this fails if numeric truncation regresses.
    # A wider value would return MALFORMED and pass a length assertion without truncating.
    result = bounded_text(10**200, 64)

    assert result.endswith(ELLIPSIS)
    assert result == str(10**200)[:64] + ELLIPSIS


def test_an_unrenderable_integer_does_not_raise() -> None:
    """`str()` on an integer wider than 4300 digits raises, inside the error-mapping path."""
    assert bounded_text(10**5000, 64) == MALFORMED


@pytest.mark.parametrize("value", [0, -1, 1.5, True, False])
def test_an_ordinary_scalar_still_renders(value: Any) -> None:
    assert bounded_text(value, 64) == str(value)


def test_an_integer_within_the_limit_is_not_truncated() -> None:
    assert bounded_text(10**60, 64) == str(10**60)


def test_whitespace_collapsing_does_not_materialise_the_whole_string() -> None:
    """`" ".join(value.split())` built every token before the limit applied.

    Eight megabytes of short words became millions of objects on an error path. The guard
    is the time taken: the result was always bounded, the work was not.
    """
    huge = "ab " * 3_000_000

    started = time.monotonic()
    result = bounded_text(huge, 64)
    elapsed = time.monotonic() - started

    assert len(result) <= 65
    assert elapsed < 1.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  hello   world  ", "hello world"),
        ("one\ttwo\nthree", "one two three"),
        ("", ""),
        ("   ", ""),
        ("single", "single"),
    ],
)
def test_whitespace_collapsing_matches_the_obvious_implementation(raw: str, expected: str) -> None:
    """The fast path must not change behaviour, only the work done to get there."""
    assert bounded_text(raw, 200) == expected


def test_an_all_whitespace_value_does_not_cost_its_full_length() -> None:
    """Bounding the output does not bound the work: whitespace produces no output.

    Without a scan budget this field is walked in full however short the limit is.
    """
    blank = " " * 50_000_000

    started = time.monotonic()
    result = bounded_text(blank, 64)
    elapsed = time.monotonic() - started

    assert len(result) <= 65
    assert elapsed < 1.0


def test_a_value_padded_past_the_scan_budget_is_marked_as_truncated() -> None:
    """The caller must be able to tell that something was dropped."""
    padded = " " * 10_000 + "visible"

    assert bounded_text(padded, 64) == ELLIPSIS


def test_an_overflowing_literal_does_not_reach_the_message_unbounded() -> None:
    """The literal is raw device text with no length of its own.

    `1.` followed by two million zeroes and `e400` is valid JSON, and `!r` copied all of it
    into an exception the capture script prints.
    """
    huge = "1." + "0" * 2_000_000 + "e400"

    with pytest.raises(ValueError, match="overflows") as caught:
        strict_json_loads('{"v": ' + huge + "}")

    assert len(str(caught.value)) < 500


def test_a_non_finite_constant_is_still_reported_by_name() -> None:
    for token in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ValueError, match="non-finite") as caught:
            strict_json_loads('{"v": ' + token + "}")
        assert token in str(caught.value)


def test_a_usable_document_still_decodes() -> None:
    assert strict_json_loads('{"a": 1, "b": 2.5, "c": [true, null]}') == {
        "a": 1,
        "b": 2.5,
        "c": [True, None],
    }
