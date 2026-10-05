"""Rendering untrusted payloads safely for errors and logs.

Device responses end up in error envelopes and log records. Two things must hold, and both
have been defects in this repository:

1. **The result must be bounded.** Bounding string values alone is not enough: a wide
   object of short values, one enormous key, or deep nesting each flood the output just as
   effectively, and per-level limits multiply rather than add.
2. **The result must be JSON-serialisable by construction.** An error envelope that cannot
   be serialised turns a reported failure into an unreported crash, which is the one thing
   the error path must never do.

This lives in the domain layer and is shared, because the same logic existed in two places
and only one of them was right.
"""

from __future__ import annotations

from typing import Any

#: Longest string preserved verbatim.
MAX_STRING_LENGTH = 200

#: Most items kept from any one collection.
MAX_ITEMS = 20

#: Deepest nesting followed.
MAX_DEPTH = 4

#: Total nodes the result may contain. Per-level limits alone are not a bound: twenty items
#: at four levels deep is a hundred and sixty thousand nodes.
MAX_NODES = 200

#: Stands in for anything that was removed, truncated or could not be represented.
ELLIPSIS = "…"
MALFORMED = "[malformed]"


def bounded_text(value: Any, limit: int = MAX_STRING_LENGTH) -> str:
    """Render a scalar within a bound, refusing shapes that would allocate first.

    `str()` on an arbitrary payload materialises the whole structure before anything is
    truncated, so a large list supplied where a name belongs would defeat the limit.
    """
    if isinstance(value, str):
        collapsed = " ".join(value.split())
        return collapsed if len(collapsed) <= limit else collapsed[:limit] + ELLIPSIS
    if isinstance(value, bool | int | float):
        return str(value)
    if value is None:
        return ""
    return MALFORMED


def bounded_payload(
    payload: Any,
    *,
    max_string: int = MAX_STRING_LENGTH,
    max_items: int = MAX_ITEMS,
    max_depth: int = MAX_DEPTH,
    max_nodes: int = MAX_NODES,
) -> Any:
    """Return a bounded, JSON-serialisable copy of an untrusted payload."""
    return _bounded(
        payload,
        depth=0,
        budget=_Budget(max_nodes),
        max_string=max_string,
        max_items=max_items,
        max_depth=max_depth,
    )


class _Budget:
    """Counts nodes consumed while building a bounded copy."""

    def __init__(self, total: int) -> None:
        self.remaining = total

    def take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


def _bounded(
    payload: Any,
    *,
    depth: int,
    budget: _Budget,
    max_string: int,
    max_items: int,
    max_depth: int,
) -> Any:
    # Budget is consumed before the depth check, not after. Short-circuiting on depth first
    # lets every node beyond the limit render for free, so a broad tree produces thousands
    # of placeholders while the budget sits untouched.
    if not budget.take() or depth >= max_depth:
        return ELLIPSIS

    if isinstance(payload, dict):
        result: dict[str, Any] = {}
        for index, (key, value) in enumerate(payload.items()):
            if index >= max_items or budget.remaining <= 0:
                result[ELLIPSIS] = f"{len(payload) - index} more keys"
                break
            # Keys are bounded too. Bounding values alone leaves one enormous key able to
            # flood the output on its own.
            result[bounded_text(key, max_string)] = _bounded(
                value,
                depth=depth + 1,
                budget=budget,
                max_string=max_string,
                max_items=max_items,
                max_depth=max_depth,
            )
        return result

    if isinstance(payload, list | tuple):
        items: list[Any] = []
        for index, value in enumerate(payload):
            if index >= max_items or budget.remaining <= 0:
                items.append(ELLIPSIS)
                break
            items.append(
                _bounded(
                    value,
                    depth=depth + 1,
                    budget=budget,
                    max_string=max_string,
                    max_items=max_items,
                    max_depth=max_depth,
                )
            )
        return items

    if isinstance(payload, str):
        return bounded_text(payload, max_string)

    # Only JSON scalars survive. Anything else -- an arbitrary object, a set, a datetime --
    # would serialise badly or not at all, and an error envelope that cannot be serialised
    # turns a reported failure into an unreported crash.
    if isinstance(payload, bool | int | float) or payload is None:
        return payload

    return MALFORMED
