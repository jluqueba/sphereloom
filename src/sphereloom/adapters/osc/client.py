"""A bounded HTTP client for the camera's Open Spherical Camera (OSC) API.

Every request to the camera goes through `OscHttpClient.request`, so the guarantees below
hold in one place:

- **Only the documented endpoints.** A request names an `OscEndpoint`; the client cannot be
  pointed at an arbitrary URL. File downloads, which follow URLs the camera returns, have
  their own origin checks elsewhere.
- **Every request is bounded in time.** httpx enforces connect, read, write and pool
  timeouts per phase, and a total deadline caps the whole exchange, so a camera dripping
  one byte per read timeout cannot hold a call open indefinitely.
- **Every response is bounded in size while it is read.** The raw body, never decompressed,
  is fed to a `PayloadBuffer`, and a declared length beyond the limit is refused unread.
- **Only HTTP 200 with a JSON object is a success.** Every other status and every transport
  failure, including one raised while the body is streaming, becomes a taxonomy error. No
  exception text from the device or the library reaches the error, and the library
  exception is never chained to it.
- **An ambiguous outcome is never retryable.** Once a request may have reached the camera,
  its failure is reported as retryable only if the caller declared the request safe to
  replay. Only a failure to connect proves that nothing was sent.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import TracebackType
from typing import Literal, Self

import httpx

from sphereloom import __version__
from sphereloom.config import Settings
from sphereloom.domain.bounded import JsonValue, bounded_json
from sphereloom.domain.errors import (
    CameraBusyError,
    InternalError,
    NotConnectedError,
    OperationTimeoutError,
    RateLimitedError,
    SphereLoomError,
)
from sphereloom.domain.payloads import MAX_PAYLOAD_BYTES, PayloadBuffer

#: Largest error body read, in bytes. An error needs only its code and message.
MAX_ERROR_BODY_BYTES = 64 * 1024

HEADERS = {
    "X-XSRF-Protected": "1",
    "Content-Type": "application/json;charset=utf-8",
    "Accept": "application/json",
    # Bodies are bounded as received; a compressed body could expand past the limit first.
    "Accept-Encoding": "identity",
    "User-Agent": f"SphereLoom/{__version__}",
}


class OscEndpoint(Enum):
    """The OSC endpoints SphereLoom calls, with their HTTP method and path."""

    INFO = ("GET", "/osc/info")
    STATE = ("POST", "/osc/state")
    EXECUTE = ("POST", "/osc/commands/execute")
    STATUS = ("POST", "/osc/commands/status")

    @property
    def method(self) -> str:
        """The HTTP method."""
        return self.value[0]

    @property
    def path(self) -> str:
        """The path below the camera's base URL."""
        return self.value[1]


@dataclass(frozen=True, slots=True)
class _Failure:
    """How a failed exchange is reported, before retryability is decided."""

    error: type[SphereLoomError]
    reason: str
    message: str
    delivered: bool
    details: tuple[tuple[str, JsonValue], ...] = ()
    #: Set where retrying cannot help whatever the request, such as a non-OSC device.
    never_retry: bool = False


class _StatusFailureError(Exception):
    """Carries a non-200 reply out of the streaming context to `request`."""

    def __init__(self, failure: _Failure) -> None:
        super().__init__(failure.reason)
        self.failure = failure


class OscHttpClient:
    """Sends requests to one camera and returns decoded JSON objects, or raises."""

    def __init__(
        self,
        base_url: str,
        *,
        connect_timeout: float,
        read_timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
        deadline_seconds: float | None = None,
        max_body_bytes: int = MAX_PAYLOAD_BYTES,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._deadline = (
            connect_timeout + read_timeout if deadline_seconds is None else deadline_seconds
        )
        self._max_body_bytes = max_body_bytes
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=HEADERS,
            timeout=httpx.Timeout(
                connect=connect_timeout, read=read_timeout, write=read_timeout, pool=connect_timeout
            ),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        """Build a client for the camera the configuration names."""
        return cls(
            settings.osc_base_url,
            connect_timeout=settings.osc_connect_timeout_seconds,
            read_timeout=settings.osc_timeout_seconds,
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Release the connection pool."""
        await self._client.aclose()

    async def request(
        self,
        endpoint: OscEndpoint,
        body: Mapping[str, JsonValue] | None = None,
        *,
        replay_safe: bool,
    ) -> dict[str, JsonValue]:
        """Send one request and return the decoded JSON object of an HTTP 200 reply.

        `replay_safe` has no default on purpose: whether repeating a request is harmless is
        a property of the request, and only the caller knows it.
        """
        failure: _Failure | None = None
        try:
            async with asyncio.timeout(self._deadline):
                return await self._exchange(endpoint, body)
        except SphereLoomError:
            raise
        except _StatusFailureError as status:
            failure = status.failure
        except TimeoutError:
            failure = _Failure(OperationTimeoutError, "deadline", _TIMED_OUT, delivered=True)
        except httpx.HTTPError as error:
            failure = _transport_failure(error)
        # Raised outside the handler, so the library exception, which may hold the request
        # and its URL, is not chained to the taxonomy error.
        raise self._error(failure, replay_safe=replay_safe)

    async def _exchange(
        self, endpoint: OscEndpoint, body: Mapping[str, JsonValue] | None
    ) -> dict[str, JsonValue]:
        async with self._client.stream(
            endpoint.method, endpoint.path, json=dict(body) if body is not None else None
        ) as response:
            if response.status_code != httpx.codes.OK:
                payload = await self._read(response, MAX_ERROR_BODY_BYTES, tolerant=True)
                raise _StatusFailureError(_status_failure(response.status_code, payload))
            payload = await self._read(response, self._max_body_bytes, tolerant=False)
        if not isinstance(payload, dict):
            message = "The camera answered with JSON that is not an object."
            raise InternalError(message, details={"reason": "unexpected_shape"})
        return payload

    @staticmethod
    async def _read(response: httpx.Response, limit: int, *, tolerant: bool) -> JsonValue:
        """Read the raw body within `limit` and decode it.

        For an error reply (`tolerant`), an unusable body yields `None` instead of raising,
        so the status still decides the error.
        """
        try:
            if _declared_length(response) > limit:
                raise InternalError(
                    "The camera's response is larger than SphereLoom accepts.",
                    details={"decode_error": "too_large", "limit_bytes": limit},
                )
            buffer = PayloadBuffer(limit)
            async for chunk in response.aiter_raw():
                buffer.feed(chunk)
            return buffer.decode()
        except InternalError:
            if tolerant:
                return None
            raise

    def _error(self, failure: _Failure, *, replay_safe: bool) -> SphereLoomError:
        message = failure.message.format(base_url=self._base_url)
        details: dict[str, JsonValue] = {"reason": failure.reason, **dict(failure.details)}
        replayable = not failure.never_retry and (replay_safe or not failure.delivered)
        retryable: Literal[False] | None = None if replayable else False
        return failure.error(message, details=details, retryable=retryable)


def _declared_length(response: httpx.Response) -> int:
    """Return the declared body length, or 0 when absent or unusable.

    Checked by length before conversion: a value longer than any real length could hold
    is treated as too large without asking `int` to convert it.
    """
    value = response.headers.get("Content-Length", "")
    # `isdigit` alone accepts characters such as "²", which `int` then refuses.
    if not (value.isascii() and value.isdigit()):
        return 0
    return int(value) if len(value) <= 18 else MAX_PAYLOAD_BYTES * 1024


_UNREACHABLE = (
    "The camera did not answer at {base_url}. Join the camera's Wi-Fi access point, check that "
    "the camera is on, and try again."
)
_DROPPED = (
    "The connection to the camera at {base_url} dropped before it answered. Check the Wi-Fi "
    "connection to the camera."
)
_TIMED_OUT = "The camera at {base_url} did not answer in time."
_POOL = "SphereLoom could not obtain a connection to the camera at {base_url} in time."
_UNEXPECTED = "SphereLoom could not complete the request to the camera at {base_url}."


def _transport_failure(error: httpx.HTTPError) -> _Failure:
    """Map an httpx exception; only a connect failure proves nothing reached the camera."""
    if isinstance(error, httpx.ConnectError | httpx.ConnectTimeout):
        return _Failure(NotConnectedError, "unreachable", _UNREACHABLE, delivered=False)
    if isinstance(error, httpx.PoolTimeout):
        return _Failure(OperationTimeoutError, "pool_timeout", _POOL, delivered=False)
    if isinstance(error, httpx.TimeoutException):
        return _Failure(OperationTimeoutError, "read_timeout", _TIMED_OUT, delivered=True)
    if isinstance(error, httpx.NetworkError | httpx.RemoteProtocolError):
        return _Failure(NotConnectedError, "connection_dropped", _DROPPED, delivered=True)
    return _Failure(InternalError, "transport_error", _UNEXPECTED, delivered=True)


def _status_failure(status: int, payload: JsonValue) -> _Failure:
    """Describe a reply other than HTTP 200.

    A status reply proves the request reached the camera, so `request` reports it as
    retryable only for a replay-safe request, and never where retrying cannot help.
    """
    details: list[tuple[str, JsonValue]] = [("http_status", status)]
    vendor = _vendor_error(payload)
    if vendor is not None:
        details.append(("vendor", vendor))

    def failure(
        error: type[SphereLoomError], reason: str, message: str, *, never_retry: bool = False
    ) -> _Failure:
        return _Failure(error, reason, message, True, tuple(details), never_retry)

    if status < 300:
        return failure(InternalError, "unexpected_status", _STATUS_MESSAGE)
    if status < 400:
        return failure(InternalError, "unexpected_redirect", _STATUS_MESSAGE)
    if vendor is not None and status in {400, 403}:
        return failure(InternalError, "vendor_error", "The camera rejected the request.")
    if status == 404:
        return failure(
            NotConnectedError,
            "not_an_osc_endpoint",
            "The device at {base_url} answered, but not as an OSC camera. Check that this "
            "machine joined the camera's Wi-Fi access point rather than another network.",
            never_retry=True,
        )
    if status == 429:
        return failure(
            RateLimitedError, "rate_limited", "The camera is receiving requests too fast."
        )
    if status == 503:
        return failure(
            CameraBusyError, "service_unavailable", "The camera is busy and cannot serve this now."
        )
    if status >= 500:
        return failure(
            NotConnectedError, "server_error", "The camera reported an internal failure."
        )
    return failure(InternalError, "unexpected_status", _STATUS_MESSAGE)


_STATUS_MESSAGE = "The camera answered with an unexpected HTTP status."


def _vendor_error(payload: JsonValue) -> dict[str, JsonValue] | None:
    """Return the bounded `{code, message}` of an OSC error body, or `None`."""
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict) or not isinstance(error.get("code"), str):
        return None
    copied = bounded_json({"code": error.get("code"), "message": error.get("message")})
    return copied if isinstance(copied, dict) else None
