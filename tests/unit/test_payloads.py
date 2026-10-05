"""Bounded, JSON-safe rendering of untrusted payloads.

This is the shared implementation behind error envelopes and captured fixtures. It existed
twice before, and only one copy was correct, which is why it now lives in one place.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from sphereloom.domain.payloads import (
    ELLIPSIS,
    MALFORMED,
    MAX_NODES,
    bounded_payload,
    bounded_text,
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
        if depth == 0:
            return "leaf"
        return {f"key{index}": branching(depth - 1) for index in range(20)}

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
