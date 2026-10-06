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

#: How many raw characters may be scanned per character of output when collapsing
#: whitespace. Bounding the output alone does not bound the work: whitespace produces no
#: output, so a field of nothing but spaces would be scanned in full whatever the limit.
MAX_SCAN_MULTIPLE = 16

#: How far back from a limit `cut_text` looks for a boundary: whitespace, or a backslash
#: that starts an escape in `repr` output. It must be at least as long as the shortest run
#: a log redaction pattern needs -- eight characters, for a bearer token -- so that a run
#: too long to back out of is always long enough to be recognised.
CUT_SEARCH = 64

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
    # rule-exempt(json): this IS the strict decoder every other caller must go through
    return json.loads(data, parse_constant=_reject_non_finite, parse_float=_finite_float)


def _reject_non_finite(constant: str) -> NoReturn:
    # The decoder only ever passes the three bare tokens, each at most nine characters.
    message = (
        "The payload contains the non-finite JSON constant "
        f"{constant!r}."  # rule-exempt(repr): the decoder passes NaN, Infinity or -Infinity
    )
    raise ValueError(message)


def _finite_float(literal: str) -> float:
    """Refuse number literals that overflow to infinity.

    `parse_constant` only ever sees the bare tokens, so a well-formed literal such as
    `1e400` bypasses it completely.

    The literal is bounded before it reaches the message. It is raw text from the device
    and has no length limit of its own: `1.` followed by two million zeroes and `e400` is
    valid JSON, and `!r` would copy all of it into an exception that the capture script
    prints and the error path renders.
    """
    value = float(literal)
    if not math.isfinite(value):
        message = (
            f"The payload contains the number literal {bounded_text(literal, 64)}, which overflows."
        )
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
        collapsed = _collapse_whitespace(value, limit)
        # A collapse that stopped on its scan budget has already ended on a word boundary
        # and marked itself. Cutting it again would drop a whole final word to make room
        # for a marker that is already there.
        if collapsed.endswith(ELLIPSIS) and len(collapsed) <= limit + 1:
            return collapsed
        return _truncate(collapsed, limit)
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
    return cut_text(text, limit)


def cut_text(text: str, limit: int) -> str:
    """Shorten `text` to at most `limit` characters, ending on a word boundary if one is near.

    Where a cut lands matters because bounding happens before log redaction. Every
    redaction pattern matches a run of characters containing no whitespace, and the
    shortest run any of them needs is eight characters. A plain slice that lands three
    characters into a bearer token leaves a prefix no pattern recognises, and that prefix
    is then logged.

    So the cut backs off to the last boundary -- whitespace, or a backslash starting an
    escape in `repr` output -- within `CUT_SEARCH` characters of the limit, which discards
    a partial token whole. If there is no boundary that close, the run being cut is at least
    `CUT_SEARCH` long, which is enough for every pattern to recognise what is kept. The
    work is bounded by `limit`, whatever the length of `text`.
    """
    if len(text) <= limit:
        return text
    return _end_on_boundary(text[:limit]) + ELLIPSIS


def _end_on_boundary(head: str) -> str:
    """Drop a trailing partial word, if a word boundary lies within `CUT_SEARCH` of the end.

    Shared by every place that stops reading part-way through a string, because each of
    them can otherwise leave a fragment of a secret that log redaction cannot recognise.
    A boundary that would leave nothing but whitespace is ignored: the run after it is then
    the whole of what was kept, and long enough to be recognised.

    A backslash counts as a boundary as well as whitespace. Text rendered with `repr`
    writes whitespace as an escape -- a newline becomes the two characters `\\n` -- so a cut
    after `repr` would otherwise find no whitespace between `Bearer` and the start of its
    token, and keep a fragment of it. In ordinary text the only effect is a slightly
    earlier cut.
    """
    for index in range(len(head) - 1, max(0, len(head) - CUT_SEARCH) - 1, -1):
        if head[index].isspace() or head[index] == "\\":
            kept = head[:index]
            return head if not kept or kept.isspace() else kept
    return head


def _collapse_whitespace(value: str, limit: int) -> str:
    """Collapse runs of whitespace, stopping once the output or the scan budget is spent.

    `" ".join(value.split())` would materialise every token of the string and then a full
    normalised copy, all before the limit applies: eight megabytes of short words becomes
    millions of Python objects on an error path.

    Two budgets are needed, not one. Stopping when the output is full bounds the result but
    not the work, because whitespace produces no output: a field of nothing but spaces is
    scanned in full however short the limit. The scan budget bounds the work itself.

    One character beyond the limit is kept so the caller can still tell whether the value
    was truncated.
    """
    scan_budget = limit * MAX_SCAN_MULTIPLE
    out: list[str] = []
    pending_space = False
    for index, char in enumerate(value):
        if index >= scan_budget:
            kept = "".join(out)
            # Reading stopped part-way through the input, which may be part-way through a
            # word. Unless the last character read was whitespace, the final word may
            # continue beyond the budget, so it goes through the same boundary rule as any
            # other cut rather than leaving a fragment for redaction to miss.
            if not pending_space:
                kept = _end_on_boundary(kept)
            return kept + ELLIPSIS
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
