"""Strict, bounded decoding of payloads received from a device.

A camera response is untrusted input: it arrives over a network from firmware SphereLoom
does not control. Every response body is decoded here and nowhere else, which a codebase
rule enforces, so each guarantee below is kept in exactly one place:

- **Size is bounded while reading, not after.** `PayloadBuffer` refuses a body as soon as it
  grows past the limit, so memory is bounded by the limit plus one chunk. Feed it the raw
  bytes as received, never content-decoded ones: one compressed chunk can expand far past
  the limit before the buffer sees it.
- **Only valid, interoperable JSON is accepted.** Python's decoder accepts `NaN` and
  `Infinity`, turns a literal such as `1e400` into infinity, and builds integers of any
  width. All of these are refused, as are integers outside the signed 64-bit range, invalid
  UTF-8 and strings with lone surrogates, which cannot be re-encoded.
- **Nesting is bounded.** Input nested deeply enough to exhaust the decoder is refused
  rather than escaping as `RecursionError`, and anything deeper than `MAX_DEPTH` is refused
  after decoding.

Every refusal is an `InternalError`, not retryable, whose `details.decode_error` names the
reason. No part of the payload is copied into the error.
"""

from __future__ import annotations

import json
import math
from typing import NoReturn

from sphereloom.domain.bounded import JsonValue
from sphereloom.domain.errors import InternalError

#: Largest response body accepted from a device, in bytes.
MAX_PAYLOAD_BYTES = 1024 * 1024

#: Deepest container nesting accepted. Documented responses are no more than five deep.
MAX_DEPTH = 32

_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
# A signed 64-bit integer has at most 19 digits plus a sign. Checking the length first
# keeps `int()` from ever converting a long literal, which costs time quadratic in its
# length and raises above 4300 digits.
_INT64_MAX_LITERAL = 20


class _RejectedError(Exception):
    """Raised inside the decoder's hooks; converted to `InternalError` by the caller."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def decode_device_json(data: bytes, *, max_bytes: int = MAX_PAYLOAD_BYTES) -> JsonValue:
    """Decode a device response body, refusing anything unsafe or not interoperable.

    Raises:
        InternalError: with `details.decode_error` set to `too_large`, `not_utf8`,
            `malformed`, `non_finite_number`, `integer_out_of_range`, `too_deep` or
            `invalid_unicode`.
    """
    if len(data) > max_bytes:
        _refuse("too_large", limit_bytes=max_bytes, size_bytes=len(data))
    # Each failure is recorded and raised after its `except` block has ended. Raising inside
    # it would chain the original exception, and `JSONDecodeError.doc` holds the whole
    # payload, which would then travel with the error into any traceback.
    failure: tuple[str, dict[str, int]] | None = None
    value: JsonValue = None
    try:
        # The one sanctioned JSON decoder; the codebase rule exempts this file by path.
        value = json.loads(
            data.decode("utf-8"),
            parse_constant=_reject_constant,
            parse_float=_finite_float,
            parse_int=_int64,
        )
    except UnicodeDecodeError:
        failure = ("not_utf8", {})
    except _RejectedError as rejected:
        failure = (rejected.reason, {})
    except RecursionError:
        failure = ("too_deep", {"max_depth": MAX_DEPTH})
    except json.JSONDecodeError as error:
        failure = ("malformed", {"position": error.pos})
    except ValueError:
        failure = ("malformed", {})
    if failure is not None:
        _refuse(failure[0], **failure[1])
    _check_structure(value)
    return value


class PayloadBuffer:
    """Accumulates a response body chunk by chunk, refusing it once it exceeds the limit.

    Feed it from a streaming read, so an oversized body is refused before it is held in
    memory in full, then call `decode`.
    """

    def __init__(self, max_bytes: int = MAX_PAYLOAD_BYTES) -> None:
        self._max_bytes = max_bytes
        self._chunks: list[bytes] = []
        self._size = 0

    def feed(self, chunk: bytes) -> None:
        """Append one chunk, or raise `InternalError` (`too_large`) if the limit is passed."""
        self._size += len(chunk)
        if self._size > self._max_bytes:
            self._chunks.clear()
            _refuse("too_large", limit_bytes=self._max_bytes)
        self._chunks.append(chunk)

    def decode(self) -> JsonValue:
        """Decode the accumulated body with `decode_device_json`.

        A buffer that refused a chunk refuses again here, rather than decoding whatever it
        held before the limit was passed.
        """
        if self._size > self._max_bytes:
            _refuse("too_large", limit_bytes=self._max_bytes)
        return decode_device_json(b"".join(self._chunks), max_bytes=self._max_bytes)


def _reject_constant(_constant: str) -> NoReturn:
    raise _RejectedError("non_finite_number")


def _finite_float(literal: str) -> float:
    # `parse_constant` only sees the bare tokens; a well-formed literal such as `1e400`
    # reaches this hook and becomes infinity.
    value = float(literal)
    if not math.isfinite(value):
        raise _RejectedError("non_finite_number")
    return value


def _int64(literal: str) -> int:
    if len(literal) > _INT64_MAX_LITERAL:
        raise _RejectedError("integer_out_of_range")
    value = int(literal)
    if not _INT64_MIN <= value <= _INT64_MAX:
        raise _RejectedError("integer_out_of_range")
    return value


def _check_structure(value: JsonValue) -> None:
    """Refuse nesting beyond `MAX_DEPTH` and strings that cannot be encoded as UTF-8.

    Iterative, so the check itself cannot exhaust the stack. Its work is bounded by the size
    of the decoded payload, which `MAX_PAYLOAD_BYTES` already bounds.
    """
    pending: list[tuple[JsonValue, int]] = [(value, 0)]
    while pending:
        node, depth = pending.pop()
        if isinstance(node, str):
            _check_text(node)
        elif isinstance(node, dict | list):
            if depth >= MAX_DEPTH:
                _refuse("too_deep", max_depth=MAX_DEPTH)
            if isinstance(node, dict):
                for key, item in node.items():
                    _check_text(key)
                    pending.append((item, depth + 1))
            else:
                pending.extend((item, depth + 1) for item in node)


def _check_text(text: str) -> None:
    # A JSON escape such as "\ud800" decodes to a lone surrogate, which every later attempt
    # to encode the string, for a log, a file or an MCP response, would fail on.
    if text.isascii():
        return
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        encodable = False
    else:
        encodable = True
    # Raised outside the handler: `UnicodeEncodeError.object` holds the whole string.
    if not encodable:
        _refuse("invalid_unicode")


def _refuse(reason: str, **limits: int) -> NoReturn:
    message = (
        "The camera returned a response SphereLoom cannot use safely "
        f"({reason.replace('_', ' ')}). This is not caused by the request; if it persists, "
        "the camera firmware may be sending data SphereLoom does not support."
    )
    raise InternalError(message, details={"decode_error": reason, **limits})
