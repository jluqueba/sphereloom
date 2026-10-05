"""Handling untrusted payloads safely: decoding them, and rendering them for errors and logs.

Device responses are decoded, then end up in error envelopes and log records. Three things
must hold, and all three have been defects in this repository:

1. **Decoding must refuse what JSON does not allow.** Python's decoder accepts `NaN` and
   `Infinity`, and renders an overflowing literal such as `1e400` as `inf`. A value that
   defeats every numeric comparison must not reach a domain model.
2. **The result must be bounded.** Bounding string values alone is not enough: a wide
   object of short values, one enormous key, or deep nesting each flood the output just as
   effectively, and per-level limits multiply rather than add.
3. **The result must be JSON-serialisable by construction.** An error envelope that cannot
   be serialised turns a reported failure into an unreported crash, which is the one thing
   the error path must never do.

This lives in the domain layer and is shared, because every part of it has at some point
existed in two places with only one of them correct.
"""

from __future__ import annotations

import json
import math
from typing import Any, NoReturn

#: Longest string preserved verbatim.
MAX_STRING_LENGTH = 200

#: Most items kept from any one collection.
MAX_ITEMS = 20

#: Deepest nesting followed.
MAX_DEPTH = 4

#: Total nodes the result may contain. Per-level limits alone are not a bound: twenty items
#: at four levels deep is a hundred and sixty thousand nodes.
MAX_NODES = 200

#: Widest integer preserved. CPython raises rather than render an integer of more than 4300
#: digits, so an unbounded value would make serialisation fail instead of producing output.
MAX_INT_BITS = 4096

#: Stands in for anything that was removed, truncated or could not be represented.
ELLIPSIS = "…"
MALFORMED = "[malformed]"


def strict_json_loads(data: bytes | str) -> Any:
    """Decode JSON, refusing values that Python accepts but the JSON format does not.

    Python's decoder accepts the bare constants `NaN`, `Infinity` and `-Infinity`, and
    quietly turns an overflowing literal such as `1e400` into `inf`. Neither is valid JSON
    and no documented command returns either. Both must be refused at the point of decode:
    a value that defeats every numeric comparison must not reach a domain model, and one
    that cannot be re-serialised must not reach a log record.

    Raises:
        ValueError: for malformed JSON, and for any non-finite number.
        RecursionError: for input nested deeply enough to exhaust the decoder.
    """
    return json.loads(data, parse_constant=_reject_non_finite, parse_float=_finite_float)


def _reject_non_finite(constant: str) -> NoReturn:
    message = f"The payload contains the non-finite JSON constant {constant!r}."
    raise ValueError(message)


def _finite_float(literal: str) -> float:
    """Refuse number literals that overflow to infinity.

    `parse_constant` only ever sees the bare tokens, so a well-formed literal such as
    `1e400` bypasses it completely.
    """
    value = float(literal)
    if not math.isfinite(value):
        message = f"The payload contains the number literal {literal!r}, which overflows."
        raise ValueError(message)
    return value


def bounded_text(value: Any, limit: int = MAX_STRING_LENGTH) -> str:
    """Render a scalar within a bound, refusing shapes that would allocate first.

    `str()` on an arbitrary payload materialises the whole structure before anything is
    truncated, so a large list supplied where a name belongs would defeat the limit. The
    same is true of a large integer: `str()` on one is neither bounded nor total, since
    CPython raises above 4300 digits rather than render it.
    """
    if isinstance(value, str):
        return _truncate(_collapse_whitespace(value, limit), limit)
    if value is None:
        return ""
    if isinstance(value, bool | float):
        return _truncate(str(value), limit)
    if isinstance(value, int):
        # Checked before rendering: `str()` on a wider integer raises rather than return a
        # long string, which would turn a reported failure into a crash.
        return _truncate(str(value), limit) if value.bit_length() <= MAX_INT_BITS else MALFORMED
    return MALFORMED


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + ELLIPSIS


def _collapse_whitespace(value: str, limit: int) -> str:
    """Collapse runs of whitespace, stopping once `limit` characters are settled.

    `" ".join(value.split())` would materialise every token of the string and then a full
    normalised copy, all before the limit applies: eight megabytes of short words becomes
    millions of Python objects on an error path. Walking the string and stopping early
    bounds the work as well as the result.

    One character beyond the limit is kept so the caller can still tell whether the value
    was truncated.
    """
    out: list[str] = []
    pending_space = False
    for char in value:
        if char.isspace():
            # Leading whitespace produces no separator, matching `split()`.
            pending_space = bool(out)
            continue
        if pending_space:
            out.append(" ")
            pending_space = False
        out.append(char)
        if len(out) > limit:
            break
    return "".join(out)


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

    # Only JSON scalars survive, and only those that actually serialise. Anything else --
    # an arbitrary object, a set, a datetime -- would serialise badly or not at all, and an
    # error envelope that cannot be serialised turns a reported failure into an unreported
    # crash. `bool` is checked before `int` because `True` is an `int`.
    if payload is None or isinstance(payload, bool):
        return payload

    if isinstance(payload, int):
        # CPython refuses to render an integer wider than 4300 digits, so a large enough
        # value from a device raises inside `json.dumps` instead of serialising.
        return payload if payload.bit_length() <= MAX_INT_BITS else MALFORMED

    if isinstance(payload, float):
        # `json.dumps` writes NaN and Infinity bare, which no JSON parser accepts, so one
        # non-finite value from a device makes the whole envelope unreadable.
        return payload if math.isfinite(payload) else MALFORMED

    return MALFORMED
