"""Bounded, JSON-serialisable copies of values that may be untrusted.

An error envelope carries details that can originate on a device. Two properties must hold
for every one of them, whatever the input:

1. **The copy is bounded, and so is the work of making it.** Every string is sliced before
   it is inspected, every collection is read only up to its item limit, nesting is cut at a
   fixed depth, and a node budget caps the whole result. Per-level limits alone are not a
   bound: they multiply rather than add.
2. **The copy is JSON-serialisable by construction.** Only JSON scalars, lists and string-
   keyed objects survive. Anything else, including a non-finite float, an integer wider
   than 64 bits and a string with a lone surrogate, is replaced by a marker. An envelope
   that cannot be serialised turns a reported failure into an unreported crash.

Nothing here redacts. Device text reaches error envelopes, which go to the MCP client, but it
never reaches a log record, so there is no pattern-based redaction to protect.
"""

from __future__ import annotations

import math

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None

#: Longest string kept from an untrusted value.
MAX_STRING_LENGTH = 200

#: Most items kept from any one list or object.
MAX_ITEMS = 20

#: Deepest container nesting kept; a container below it is replaced by `ELLIPSIS`.
MAX_DEPTH = 4

#: Most nodes, containers and scalars together, in one bounded copy.
MAX_NODES = 200

#: Stands in for anything removed or cut short.
ELLIPSIS = "…"

#: Stands in for a value that cannot be represented safely in JSON.
UNREPRESENTABLE = "[unrepresentable]"

_MAX_INT_BITS = 64


def bounded_text(value: str, limit: int = MAX_STRING_LENGTH) -> str:
    """Return at most `limit` characters of `value`, marked when cut, encodable as UTF-8.

    The slice is taken first, so the work is proportional to `limit` rather than to the
    length of `value`. A lone surrogate cannot be encoded as UTF-8 and would make the
    envelope fail to serialise, so each one is replaced.
    """
    head = value[: limit + 1]
    truncated = len(head) > limit
    head = str(head[:limit])
    if not head.isascii():
        head = head.encode("utf-8", "replace").decode("utf-8")
    return head + ELLIPSIS if truncated else head


def bounded_json(
    value: object,
    *,
    max_string: int = MAX_STRING_LENGTH,
    max_items: int = MAX_ITEMS,
    max_depth: int = MAX_DEPTH,
    max_nodes: int = MAX_NODES,
) -> JsonValue:
    """Return a bounded copy of `value` that `json.dumps(..., allow_nan=False)` accepts."""
    limits = _Limits(max_string, max_items, max_depth, max_nodes)
    return _copy(value, depth=0, limits=limits)


class _Limits:
    """The limits of one copy, and the node budget it has left."""

    def __init__(self, max_string: int, max_items: int, max_depth: int, max_nodes: int) -> None:
        self.max_string = max_string
        self.max_items = max_items
        self.max_depth = max_depth
        self.remaining = max_nodes

    def take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


def _copy(value: object, *, depth: int, limits: _Limits) -> JsonValue:
    # The budget is spent before anything else, so nodes past a cut still count and a broad
    # tree cannot produce thousands of markers for free.
    if not limits.take():
        return ELLIPSIS
    if isinstance(value, dict):
        return (
            _copy_object(value, depth=depth, limits=limits)
            if depth < limits.max_depth
            else ELLIPSIS
        )
    if isinstance(value, list | tuple):
        return (
            _copy_array(value, depth=depth, limits=limits) if depth < limits.max_depth else ELLIPSIS
        )
    return _scalar(value, limits)


def _copy_object(value: dict[object, object], *, depth: int, limits: _Limits) -> JsonValue:
    result: dict[str, JsonValue] = {}
    for index, (key, item) in enumerate(value.items()):
        if index >= limits.max_items or limits.remaining <= 0:
            result[ELLIPSIS] = f"{len(value) - index} more"
            break
        # Keys are bounded too: one enormous key would otherwise flood the copy on its own.
        name = bounded_text(key, limits.max_string) if isinstance(key, str) else UNREPRESENTABLE
        result[name] = _copy(item, depth=depth + 1, limits=limits)
    return result


def _copy_array(
    value: list[object] | tuple[object, ...], *, depth: int, limits: _Limits
) -> JsonValue:
    result: list[JsonValue] = []
    for index, item in enumerate(value):
        if index >= limits.max_items or limits.remaining <= 0:
            result.append(ELLIPSIS)
            break
        result.append(_copy(item, depth=depth + 1, limits=limits))
    return result


def _scalar(value: object, limits: _Limits) -> JsonValue:
    # Subclasses are converted to the plain type, so an enum or a custom `str` cannot carry
    # its own serialisation behaviour into the envelope. `bool` is tested before `int`
    # because `True` is an `int`.
    if value is None:
        return None
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        return bounded_text(value, limits.max_string)
    if isinstance(value, int):
        # Checked before conversion: CPython refuses to render an integer of more than 4300
        # digits, so a wide enough value would raise inside the serialiser.
        return int(value) if value.bit_length() <= _MAX_INT_BITS else UNREPRESENTABLE
    if isinstance(value, float):
        # `json.dumps` writes NaN and Infinity bare, which no JSON parser accepts.
        return float(value) if math.isfinite(value) else UNREPRESENTABLE
    return UNREPRESENTABLE
