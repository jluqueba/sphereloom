"""Strict, bounded decoding of device payloads (ADR-0017, AC-10.5).

A camera response is untrusted. Each test feeds the decoder something a faulty or hostile
device could send and checks that the outcome is a typed, non-retryable `internal` error
naming the reason, never an unmapped exception and never a value a later step cannot use.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import NoReturn

import pytest

from sphereloom.domain.errors import ErrorCode, InternalError
from sphereloom.domain.payloads import (
    MAX_DEPTH,
    MAX_PAYLOAD_BYTES,
    PayloadBuffer,
    decode_device_json,
)


def _refusal(data: bytes, *, max_bytes: int = MAX_PAYLOAD_BYTES) -> InternalError:
    with pytest.raises(InternalError) as caught:
        decode_device_json(data, max_bytes=max_bytes)
    return caught.value


def _feed_all(buffer: PayloadBuffer, chunks: Iterator[bytes]) -> None:
    for chunk in chunks:
        buffer.feed(chunk)


def _nested(depth: int) -> bytes:
    return b"[" * depth + b"]" * depth


def test_a_well_formed_response_is_decoded() -> None:
    body = b'{"state": "done", "results": {"entries": [{"size": 1024, "ratio": 0.5}]}}'

    assert decode_device_json(body) == {
        "state": "done",
        "results": {"entries": [{"size": 1024, "ratio": 0.5}]},
    }


def test_a_body_over_the_size_limit_is_refused_before_it_is_parsed() -> None:
    """Invalid JSON over the limit reports `too_large`, so the size check ran first."""
    error = _refusal(b"x" * 101, max_bytes=100)

    assert error.details == {"decode_error": "too_large", "limit_bytes": 100, "size_bytes": 101}


def test_a_body_exactly_at_the_size_limit_is_accepted() -> None:
    body = b'"' + b"a" * 98 + b'"'

    assert decode_device_json(body, max_bytes=100) == "a" * 98


@pytest.mark.parametrize(
    "body",
    [b"NaN", b"Infinity", b"-Infinity", b"[1e400]", b'{"x": -1e400}'],
    ids=["nan", "infinity", "negative-infinity", "overflowing", "negative-overflowing"],
)
def test_a_non_finite_number_is_refused(body: bytes) -> None:
    assert _refusal(body).details == {"decode_error": "non_finite_number"}


@pytest.mark.parametrize(
    "body",
    [str(2**63).encode(), str(-(2**63) - 1).encode(), b"1" * 5000],
    ids=["above-int64", "below-int64", "beyond-the-digit-limit"],
)
def test_an_integer_outside_the_signed_64_bit_range_is_refused(body: bytes) -> None:
    assert _refusal(body).details == {"decode_error": "integer_out_of_range"}


def test_the_signed_64_bit_extremes_are_accepted() -> None:
    body = f"[{2**63 - 1}, {-(2**63)}]".encode()

    assert decode_device_json(body) == [2**63 - 1, -(2**63)]


def test_invalid_utf8_is_refused() -> None:
    assert _refusal(b'"\xff"').details == {"decode_error": "not_utf8"}


@pytest.mark.parametrize(
    "body",
    [b'"\\ud800"', b'{"\\udc00": 1}', b'["ok", ["\\ud83d"]]'],
    ids=["value", "key", "nested"],
)
def test_a_lone_surrogate_is_refused(body: bytes) -> None:
    assert _refusal(body).details == {"decode_error": "invalid_unicode"}


def test_an_escaped_surrogate_pair_is_accepted() -> None:
    assert decode_device_json(b'"\\ud83d\\ude00"') == "\U0001f600"


def test_nesting_at_the_depth_limit_is_accepted() -> None:
    value = decode_device_json(_nested(MAX_DEPTH))

    for _ in range(MAX_DEPTH - 1):
        assert isinstance(value, list)
        value = value[0]
    assert value == []


def test_nesting_one_level_past_the_depth_limit_is_refused() -> None:
    error = _refusal(_nested(MAX_DEPTH + 1))

    assert error.details == {"decode_error": "too_deep", "max_depth": MAX_DEPTH}


def test_nesting_deep_enough_to_exhaust_the_decoder_is_a_typed_error() -> None:
    assert _refusal(_nested(100_000)).details["decode_error"] == "too_deep"


def test_a_recursion_error_from_the_decoder_is_a_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Injected, so the guarantee holds whatever depth a given interpreter can decode."""

    def exhausted(*_args: object, **_kwargs: object) -> NoReturn:
        raise RecursionError

    monkeypatch.setattr(json, "loads", exhausted)

    assert _refusal(b"[]").details == {"decode_error": "too_deep", "max_depth": MAX_DEPTH}


@pytest.mark.parametrize("body", [b"", b"{", b'{"a": }', b"\xef\xbb\xbf{}"])
def test_malformed_json_is_refused(body: bytes) -> None:
    assert _refusal(body).details["decode_error"] == "malformed"


def test_a_refusal_is_internal_and_not_retryable() -> None:
    envelope = _refusal(b"{").to_envelope()["error"]

    assert isinstance(envelope, dict)
    assert envelope["code"] == ErrorCode.INTERNAL.value
    assert envelope["retryable"] is False


@pytest.mark.parametrize(
    "body",
    [b'{"serial": "SECRET-1234" ', b'"SECRET-1234\xff"', b'"SECRET-1234\\ud800"'],
    ids=["malformed", "not-utf8", "invalid-unicode"],
)
def test_a_refusal_carries_no_part_of_the_payload(body: bytes) -> None:
    """Neither the envelope nor a chained exception may hold the device's text.

    `JSONDecodeError.doc`, `UnicodeDecodeError.object` and `UnicodeEncodeError.object` each
    hold the whole input, so an error raised inside their handler would carry it along.
    """
    error = _refusal(body)

    assert "SECRET" not in json.dumps(error.to_envelope())
    assert error.__context__ is None
    assert error.__cause__ is None


def test_the_buffer_stops_reading_as_soon_as_the_limit_is_passed() -> None:
    pulled = 0

    def chunks() -> Iterator[bytes]:
        nonlocal pulled
        for _ in range(1000):
            pulled += 1
            yield b"x" * 4

    buffer = PayloadBuffer(max_bytes=10)
    with pytest.raises(InternalError) as caught:
        _feed_all(buffer, chunks())

    assert caught.value.details == {"decode_error": "too_large", "limit_bytes": 10}
    assert pulled == 3


def test_a_buffer_that_refused_a_chunk_refuses_to_decode() -> None:
    buffer = PayloadBuffer(max_bytes=4)
    buffer.feed(b"[1]")
    with pytest.raises(InternalError):
        buffer.feed(b", 2]")

    with pytest.raises(InternalError) as caught:
        buffer.decode()

    assert caught.value.details["decode_error"] == "too_large"


def test_the_buffer_decodes_a_body_received_in_chunks() -> None:
    buffer = PayloadBuffer()
    for chunk in (b'{"sta', b'te": "do', b'ne"}'):
        buffer.feed(chunk)

    assert buffer.decode() == {"state": "done"}
