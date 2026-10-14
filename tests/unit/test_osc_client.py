"""The bounded OSC HTTP client against injected responses and failures (#16).

Every failure is injected through `httpx.MockTransport`, so the tests assert the client's
guarantees rather than a platform's network behaviour, and nothing reaches the network.
"""

from __future__ import annotations

import asyncio
import gzip
import json
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from sphereloom.adapters.osc.client import (
    MAX_ERROR_BODY_BYTES,
    OscEndpoint,
    OscHttpClient,
)
from sphereloom.config import Settings
from sphereloom.domain.errors import ErrorCode, SphereLoomError

BASE_URL = "http://192.168.42.1"
UNTRUSTED = "device-text-alice-holiday"

type Handler = Callable[[httpx.Request], httpx.Response]


class _Stream(httpx.AsyncByteStream):
    """A response body produced chunk by chunk, counting what is read."""

    def __init__(self, chunks: list[bytes], *, fail_after: int | None = None) -> None:
        self._chunks = chunks
        self._fail_after = fail_after
        self.pulled = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for index, chunk in enumerate(self._chunks):
            if self._fail_after is not None and index >= self._fail_after:
                raise httpx.ReadError(UNTRUSTED)
            self.pulled += 1
            yield chunk


def _client(handler: Handler, **kwargs: float | int) -> OscHttpClient:
    return OscHttpClient(
        BASE_URL,
        connect_timeout=5,
        read_timeout=15,
        transport=httpx.MockTransport(handler),
        **kwargs,  # type: ignore[arg-type]  # only numeric options are passed
    )


def _json(status: int, payload: object, **kwargs: object) -> httpx.Response:
    return httpx.Response(status, stream=_Stream([json.dumps(payload).encode()]), **kwargs)  # type: ignore[arg-type]  # headers only


def _raising(error: Exception) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return handler


async def _failure(
    handler: Handler, *, replay_safe: bool = False, **kwargs: float | int
) -> SphereLoomError:
    async with _client(handler, **kwargs) as client:
        with pytest.raises(SphereLoomError) as caught:
            await client.request(
                OscEndpoint.EXECUTE, {"name": "camera.takePicture"}, replay_safe=replay_safe
            )
    return caught.value


async def test_a_request_carries_the_protocol_headers_and_timeouts() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _json(200, {"state": "done"})

    async with _client(handler) as client:
        result = await client.request(
            OscEndpoint.EXECUTE, {"name": "camera.getOptions"}, replay_safe=True
        )

    (request,) = seen
    assert result == {"state": "done"}
    assert request.method == "POST"
    assert str(request.url) == f"{BASE_URL}/osc/commands/execute"
    assert request.headers["X-XSRF-Protected"] == "1"
    assert request.headers["Accept-Encoding"] == "identity"
    assert json.loads(request.content) == {"name": "camera.getOptions"}
    assert request.extensions["timeout"] == {"connect": 5, "read": 15, "write": 15, "pool": 5}


async def test_the_info_endpoint_is_a_get_without_a_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _json(200, {"model": "X5"})

    async with _client(handler) as client:
        await client.request(OscEndpoint.INFO, replay_safe=True)

    assert seen[0].method == "GET"
    assert seen[0].url.path == "/osc/info"
    assert seen[0].content == b""


def test_the_client_is_built_from_the_configuration(settings: Settings) -> None:
    client = OscHttpClient.from_settings(settings)

    assert client._base_url == "http://192.168.42.1"


async def test_a_body_is_refused_as_soon_as_it_exceeds_the_limit() -> None:
    stream = _Stream([b" " * 1024] * 100)

    error = await _failure(lambda _: httpx.Response(200, stream=stream), max_body_bytes=4096)

    assert error.code is ErrorCode.INTERNAL
    assert error.details["decode_error"] == "too_large"
    assert stream.pulled == 5


async def test_a_declared_length_beyond_the_limit_is_refused_unread() -> None:
    stream = _Stream([b"{}"])

    error = await _failure(
        lambda _: httpx.Response(
            200, stream=stream, headers={"Content-Length": "99999999999999999999999"}
        ),
        max_body_bytes=4096,
    )

    assert error.details["decode_error"] == "too_large"
    assert stream.pulled == 0


@pytest.mark.parametrize(
    "declared",
    [b"\xb2", b"-1", b"1e3", b"", b" 12"],
    ids=["superscript-digit", "negative", "exponent", "empty", "padded"],
)
async def test_a_hostile_declared_length_never_escapes_as_an_untyped_error(
    declared: bytes,
) -> None:
    """The body bound still applies; the header only ever narrows it."""
    response = httpx.Response(200, stream=_Stream([b'{"state": "done"}']))
    response.headers = httpx.Headers([(b"Content-Length", declared)])

    async with _client(lambda _: response) as client:
        result = await client.request(OscEndpoint.INFO, replay_safe=True)

    assert result == {"state": "done"}


async def test_a_compressed_body_is_never_decompressed() -> None:
    body = gzip.compress(json.dumps({"state": "done"}).encode())

    error = await _failure(
        lambda _: httpx.Response(200, stream=_Stream([body]), headers={"Content-Encoding": "gzip"})
    )

    assert error.details["decode_error"] == "not_utf8"


async def test_an_error_body_is_read_within_its_own_limit() -> None:
    stream = _Stream([b" " * 1024] * 200)

    error = await _failure(lambda _: httpx.Response(500, stream=stream))

    assert error.code is ErrorCode.NOT_CONNECTED
    assert stream.pulled <= MAX_ERROR_BODY_BYTES // 1024 + 1


async def test_a_stalled_exchange_ends_at_the_total_deadline() -> None:
    """A body that never completes is cut by the deadline, not by the per-read timeout."""
    stall = asyncio.Event()

    class _Stalled(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"{"
            await stall.wait()

    error = await _failure(lambda _: httpx.Response(200, stream=_Stalled()), deadline_seconds=0.05)

    assert error.code is ErrorCode.TIMEOUT
    assert error.details["reason"] == "deadline"


@pytest.mark.parametrize(
    ("response", "code", "reason"),
    [
        (lambda: _json(200, [1, 2]), ErrorCode.INTERNAL, "unexpected_shape"),
        (lambda: _json(204, {}), ErrorCode.INTERNAL, "unexpected_status"),
        (
            lambda: _json(302, {}, headers={"Location": "http://elsewhere.invalid/"}),
            ErrorCode.INTERNAL,
            "unexpected_redirect",
        ),
        (
            lambda: _json(400, {"error": {"code": "invalidParameterValue", "message": "x"}}),
            ErrorCode.INTERNAL,
            "vendor_error",
        ),
        (
            lambda: _json(403, {"error": {"code": "disabledCommand", "message": "x"}}),
            ErrorCode.INTERNAL,
            "vendor_error",
        ),
        (lambda: _json(400, {"unrelated": True}), ErrorCode.INTERNAL, "unexpected_status"),
        (lambda: _json(404, {}), ErrorCode.NOT_CONNECTED, "not_an_osc_endpoint"),
        (lambda: _json(429, {}), ErrorCode.RATE_LIMITED, "rate_limited"),
        (lambda: _json(503, {}), ErrorCode.CAMERA_BUSY, "service_unavailable"),
        (lambda: _json(500, {}), ErrorCode.NOT_CONNECTED, "server_error"),
        (
            lambda: httpx.Response(502, stream=_Stream([b"<html>"])),
            ErrorCode.NOT_CONNECTED,
            "server_error",
        ),
    ],
    ids=[
        "200-not-an-object",
        "204",
        "302-redirect",
        "400-vendor",
        "403-vendor",
        "400-without-vendor-body",
        "404",
        "429",
        "503",
        "500",
        "502-html",
    ],
)
async def test_only_a_200_object_succeeds_and_every_status_has_a_code(
    response: Callable[[], httpx.Response], code: ErrorCode, reason: str
) -> None:
    error = await _failure(lambda _: response(), replay_safe=True)

    assert error.code is code
    assert error.details["reason"] == reason


async def test_a_vendor_error_is_preserved_bounded() -> None:
    long_message = UNTRUSTED * 1000

    error = await _failure(
        lambda _: _json(400, {"error": {"code": "invalidParameterValue", "message": long_message}})
    )

    vendor = error.details["vendor"]
    assert isinstance(vendor, dict)
    assert vendor["code"] == "invalidParameterValue"
    assert len(str(vendor["message"])) < len(long_message)
    assert error.details["http_status"] == 400


@pytest.mark.parametrize(
    ("raised", "code", "reason", "delivered"),
    [
        (httpx.ConnectError(UNTRUSTED), ErrorCode.NOT_CONNECTED, "unreachable", False),
        (httpx.ConnectTimeout(UNTRUSTED), ErrorCode.NOT_CONNECTED, "unreachable", False),
        (httpx.PoolTimeout(UNTRUSTED), ErrorCode.TIMEOUT, "pool_timeout", False),
        (httpx.ReadTimeout(UNTRUSTED), ErrorCode.TIMEOUT, "read_timeout", True),
        (httpx.WriteTimeout(UNTRUSTED), ErrorCode.TIMEOUT, "read_timeout", True),
        (httpx.ReadError(UNTRUSTED), ErrorCode.NOT_CONNECTED, "connection_dropped", True),
        (httpx.WriteError(UNTRUSTED), ErrorCode.NOT_CONNECTED, "connection_dropped", True),
        (httpx.RemoteProtocolError(UNTRUSTED), ErrorCode.NOT_CONNECTED, "connection_dropped", True),
        (httpx.UnsupportedProtocol(UNTRUSTED), ErrorCode.INTERNAL, "transport_error", True),
        (httpx.DecodingError(UNTRUSTED), ErrorCode.INTERNAL, "transport_error", True),
    ],
    ids=lambda value: type(value).__name__ if isinstance(value, Exception) else str(value),
)
@pytest.mark.parametrize("replay_safe", [True, False], ids=["replay-safe", "not-replay-safe"])
async def test_every_transport_failure_maps_onto_the_taxonomy(
    raised: Exception, code: ErrorCode, reason: str, delivered: bool, replay_safe: bool
) -> None:
    """Retryable only if nothing reached the camera, or the request is safe to replay."""
    error = await _failure(_raising(raised), replay_safe=replay_safe)

    assert error.code is code
    assert error.details["reason"] == reason
    expected = ERROR_DEFAULT_RETRYABLE[code] and (replay_safe or not delivered)
    assert error.retryable is expected


ERROR_DEFAULT_RETRYABLE = {
    ErrorCode.NOT_CONNECTED: True,
    ErrorCode.TIMEOUT: True,
    ErrorCode.INTERNAL: False,
    ErrorCode.CAMERA_BUSY: True,
    ErrorCode.RATE_LIMITED: True,
}


@pytest.mark.parametrize("status", [429, 500, 503])
@pytest.mark.parametrize("replay_safe", [True, False], ids=["replay-safe", "not-replay-safe"])
async def test_a_status_reply_is_retryable_only_for_a_replay_safe_request(
    status: int, replay_safe: bool
) -> None:
    """A status reply proves the camera received the request, which may have run."""
    error = await _failure(lambda _: _json(status, {}), replay_safe=replay_safe)

    assert error.retryable is replay_safe


async def test_a_non_osc_device_is_never_retryable() -> None:
    error = await _failure(lambda _: _json(404, {}), replay_safe=True)

    assert error.retryable is False


async def test_a_failure_while_the_body_streams_is_mapped() -> None:
    stream = _Stream([b'{"state": ', b'"done"}'], fail_after=1)

    error = await _failure(lambda _: httpx.Response(200, stream=stream))

    assert error.code is ErrorCode.NOT_CONNECTED
    assert error.details["reason"] == "connection_dropped"
    assert error.retryable is False


async def test_an_unreachable_camera_names_its_address_and_the_fix() -> None:
    """AC-1.1.2."""
    error = await _failure(_raising(httpx.ConnectError(UNTRUSTED)))

    assert "192.168.42.1" in error.message
    assert "Wi-Fi" in error.message


@pytest.mark.parametrize(
    "handler",
    [
        _raising(httpx.ReadError(UNTRUSTED)),
        lambda _: _json(500, {"error": {"code": "x", "message": UNTRUSTED}}),
        lambda _: httpx.Response(200, stream=_Stream([b'{"x": "' + UNTRUSTED.encode() + b'" '])),
    ],
    ids=["transport", "status", "decode"],
)
async def test_no_device_or_library_text_reaches_the_message_or_the_cause(handler: Handler) -> None:
    error = await _failure(handler)

    assert UNTRUSTED not in error.message
    assert error.__cause__ is None
    assert not isinstance(error.__context__, httpx.HTTPError)
