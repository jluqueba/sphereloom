"""The HTTP client that talks to a camera over its access point.

Three constraints from the vendor's documentation shape this file, and each is enforced
here rather than left to callers to remember:

1. A static `X-XSRF-Protected: 1` header is required on every request. There is no other
   authentication; possession of the Wi-Fi credentials is the only gate.
2. Never send a command before the previous one has responded. A single lock enforces it,
   because "please remember to await" is not a safety mechanism.
3. `/osc/info` should not be polled more than once per second. A small cache enforces that
   and reports the age of what it returns, so a caller can tell fresh from remembered.

Retries are deliberately narrow, and the distinction is between three kinds of failure
rather than between two kinds of command:

* **Setup failures** -- connect timeouts, pool timeouts, connection errors. The camera
  never saw the request, so repeating it is unambiguously safe.
* **Completed server responses** -- 500, 502, 503, 504 for a command on the safe-command
  allowlist. The camera did see the request, and a 5xx does not prove it did nothing. The
  retry is safe because the allowlist contains only side-effect-free reads, such as a
  listing or an options read, which can be repeated whatever the camera did the first time.
* **Ambiguous post-send failures** -- a read or write timeout, a dropped connection after
  transmission. The request arrived and the camera may be acting on it right now. These are
  never retried automatically, and when the command changes state they are reported as
  non-retryable so a caller is not invited to duplicate a capture or a delete.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from sphereloom import __version__
from sphereloom.adapters.osc.errors import map_vendor_error
from sphereloom.domain.clock import Monotonic, SystemMonotonic
from sphereloom.domain.errors import (
    InternalError,
    NotConnectedError,
    NotFoundError,
    OperationTimeoutError,
    RateLimitedError,
)
from sphereloom.domain.payloads import bounded_text, strict_json_loads
from sphereloom.logging import get_logger

logger = get_logger("adapters.osc.client")

BACKEND = "osc"

INFO_PATH = "/osc/info"
STATE_PATH = "/osc/state"
EXECUTE_PATH = "/osc/commands/execute"
STATUS_PATH = "/osc/commands/status"

#: Required on every request. The protocol defines no other access control.
XSRF_HEADER = "X-XSRF-Protected"
XSRF_VALUE = "1"

#: The vendor's guidance is at most one `/osc/info` request per second.
INFO_MIN_INTERVAL_SECONDS = 1.0

#: Commands that are safe to repeat. Anything absent is treated as unsafe, so a command
#: added later cannot silently inherit retries by omission.
#:
#: The decision lives here rather than in a caller-supplied flag. A guarantee that "captures
#: never retry" is only as good as every call site remembering to say so, and the cost of
#: one forgotten argument is a duplicated photograph or a repeated delete.
RETRY_SAFE_COMMANDS = frozenset(
    {
        "camera.getOptions",
        "camera.listFiles",
    }
)

#: Retries apply only to requests that are safe to repeat.
MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 0.25
BACKOFF_MAX_SECONDS = 2.0

#: Largest JSON response SphereLoom will read into memory. A listing with a thousand files
#: is well under a megabyte, so this is generous; the point is that it is finite. The camera
#: is an unauthenticated device on a network SphereLoom does not control, and reading an
#: unbounded body from it would let a hostile or broken responder exhaust memory.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class CachedInfo:
    """A remembered `/osc/info` response and how stale it is."""

    payload: dict[str, Any]
    age_seconds: float


def _is_safe_to_repeat(command: str | None) -> bool:
    """Whether an agent may repeat this call without risking a duplicate side effect.

    A read has no side effect, so repeating it is harmless. Anything that captures, changes
    a setting or deletes may already have taken effect, so reporting it as retryable would
    invite the duplicate the retry policy exists to prevent.
    """
    return command is None or command in RETRY_SAFE_COMMANDS


class OscHttpClient:
    """Transport for the OSC protocol.

    Owns the connection, the command lock and the info cache. It deliberately knows nothing
    about domain models: translating payloads is the adapter's job, so this layer stays
    easy to reason about when a camera misbehaves.
    """

    def __init__(
        self,
        base_url: str,
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 15.0,
        monotonic: Monotonic | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._monotonic = SystemMonotonic() if monotonic is None else monotonic
        # The *client* is constructed here rather than injected, because accepting an
        # outside client would let a caller bypass every protocol setting below -- the
        # mandatory header, the explicit timeouts, the redirect policy -- while this class
        # still claimed to enforce them.
        #
        # A *transport* may be supplied, which is a different thing: it replaces only how
        # bytes reach the network, leaving every protocol guarantee intact. That is what
        # makes transport-level failures testable without reopening the hole.
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            transport=transport,
            headers={
                XSRF_HEADER: XSRF_VALUE,
                "Content-Type": "application/json;charset=utf-8",
                "Accept": "application/json",
                # Asked for uncompressed bodies, and anything else is refused on arrival:
                # httpx decompresses before yielding a chunk, so a small compressed body
                # could expand far past the response limit before it was measured.
                "Accept-Encoding": "identity",
                "User-Agent": f"SphereLoom/{__version__}",
            },
            timeout=httpx.Timeout(
                connect=connect_timeout, read=read_timeout, write=read_timeout, pool=connect_timeout
            ),
            # The camera is a link-local device on its own access point. Honouring system
            # proxy settings would send its traffic somewhere it cannot go, and following
            # redirects would let a surprising response steer us off the device entirely.
            follow_redirects=False,
            trust_env=False,
        )

        #: Serialises command execution. The vendor advises strongly against overlapping
        #: commands, and a lock is the only way to guarantee it regardless of caller.
        self._command_lock = asyncio.Lock()
        #: Serialises `/osc/info` separately, so concurrent callers cannot each miss an
        #: empty cache and issue a burst of requests inside one vendor window.
        self._info_lock = asyncio.Lock()
        self._info_cache: dict[str, Any] | None = None
        #: When the last request was *attempted*, which is what the throttle measures.
        self._info_attempted_at: float | None = None
        #: When the cache was last *populated*, which is what freshness measures. The two
        #: differ whenever a refresh fails, and conflating them reports stale data as new.
        self._info_cached_at: float | None = None

    @property
    def base_url(self) -> str:
        return self._base_url

    async def aclose(self) -> None:
        """Release the connection pool."""
        await self._client.aclose()

    # ------------------------------------------------------------------ endpoints

    async def info(self, *, force_refresh: bool = False) -> CachedInfo:
        """Read camera identity, honouring the vendor's one-per-second guidance.

        Returns the age of the payload so a caller can decide whether remembered data is
        good enough, rather than being silently handed something stale.

        `force_refresh` asks for a live reading rather than a cached one. It **waits** for
        the remaining interval instead of skipping it: the throttle is a protocol
        constraint, not a performance optimisation, so no caller gets to opt out of it.
        """
        # Serialised so concurrent callers cannot all miss an empty cache and issue a burst
        # of requests inside one vendor window. The cache is re-checked after acquiring,
        # since whoever held the lock has probably just populated it.
        async with self._info_lock:
            if not force_refresh:
                cached = self._cached_info()
                if cached is not None:
                    return cached

            payload = await self._request_json(
                "GET", INFO_PATH, retry_server_errors=True, before_attempt=self._await_info_window
            )
            self._info_cache = payload
            self._info_cached_at = self._monotonic.elapsed()
            return CachedInfo(payload=payload, age_seconds=0.0)

    async def _await_info_window(self) -> None:
        """Wait until the vendor's minimum interval has elapsed, then claim the window.

        Called before every attempt, retries included, so a burst of retries cannot slip
        several requests into one window.
        """
        if self._info_attempted_at is not None:
            remaining = INFO_MIN_INTERVAL_SECONDS - (
                self._monotonic.elapsed() - self._info_attempted_at
            )
            if remaining > 0:
                await asyncio.sleep(remaining)
        self._info_attempted_at = self._monotonic.elapsed()

    def _cached_info(self) -> CachedInfo | None:
        """The remembered payload, if it is still inside the vendor window.

        Freshness is measured from when the cache was *populated*, not from the last
        attempt. Those differ whenever a refresh fails, and conflating them would let a
        stale payload be reported as less than a second old after every failure.
        """
        if self._info_cache is None or self._info_cached_at is None:
            return None
        age = self._monotonic.elapsed() - self._info_cached_at
        if age >= INFO_MIN_INTERVAL_SECONDS:
            return None
        return CachedInfo(payload=self._info_cache, age_seconds=age)

    async def state(self) -> dict[str, Any]:
        """Read battery, storage and capture state."""
        return await self._request_json("POST", STATE_PATH, retry_server_errors=True)

    async def execute(
        self,
        name: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run one command, serialised against every other command.

        Whether the command may be retried is decided here, from `RETRY_SAFE_COMMANDS`, not
        by the caller. A capture, a setting change or a delete is never repeated: the camera
        may well have acted before the response was lost, and a duplicated shot is worse
        than a clear error.
        """
        body: dict[str, Any] = {"name": name}
        if parameters:
            body["parameters"] = dict(parameters)

        async with self._command_lock:
            return await self._request_json(
                "POST",
                EXECUTE_PATH,
                json=body,
                retry_server_errors=name in RETRY_SAFE_COMMANDS,
                command=name,
                vendor_errors_to_caller=True,
            )

    async def command_status(self, command_id: str) -> dict[str, Any]:
        """Poll a previously accepted command.

        Not taken under the command lock: polling is a read, and holding the lock while
        waiting for a capture would block every other call for the duration of the capture.
        """
        return await self._request_json(
            "POST",
            STATUS_PATH,
            json={"id": command_id},
            retry_server_errors=True,
            vendor_errors_to_caller=True,
        )

    @asynccontextmanager
    async def stream(self, url: str) -> AsyncIterator[httpx.Response]:
        """Open a streaming download of a file the camera advertised.

        The URL is validated against the configured camera origin first. It arrives in a
        device response, and a malformed or hostile payload could otherwise point anywhere;
        following it would send this client's requests, and its headers, to a host the
        operator never chose.

        Every transport failure is mapped to the taxonomy, including ones raised while the
        caller iterates the body: a dropped transfer half way through a download is the
        normal case on a weak access point, and a consumer should not have to catch
        third-party exception types to handle it.

        Separate from the JSON path because media files are routinely gigabytes: they are
        never buffered, parsed, or logged.
        """
        target = self._validated_url(url)

        try:
            request = self._client.build_request("GET", target)
            response = await self._client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            raise OperationTimeoutError(
                "The camera did not start sending the file within the timeout.",
                backend=BACKEND,
            ) from exc
        except httpx.HTTPError as exc:
            raise NotConnectedError(
                "Lost contact with the camera while starting a download. Check that this "
                "machine is still on the camera's Wi-Fi access point.",
                backend=BACKEND,
            ) from exc

        try:
            if response.status_code == 404:
                raise NotFoundError(
                    "The camera no longer has that file. List the gallery again to get "
                    "current file URLs.",
                    backend=BACKEND,
                )
            if response.status_code == 429:
                raise RateLimitedError(
                    "The camera is rejecting requests as too frequent. Slow down and retry.",
                    backend=BACKEND,
                )
            if response.status_code >= 500:
                # The same reading as a command: a server error while the camera restarts
                # or is overloaded. A download is a GET, so repeating it is always safe.
                raise NotConnectedError(
                    f"The camera returned HTTP {response.status_code} for the download. It "
                    "may be restarting or busy.",
                    backend=BACKEND,
                    details={"status_code": response.status_code},
                    retryable=True,
                )
            if not response.is_success:
                # Any other status outside 2xx, redirects included, is neither file content
                # nor a usable vendor answer. Redirects are deliberately not followed, so
                # persisting a 3xx body would write a redirect page to disk under a media
                # filename. Not retryable: a redirect comes back the same way every time.
                raise InternalError(
                    f"The camera answered the download with HTTP {response.status_code} "
                    "instead of file content.",
                    backend=BACKEND,
                    details={"status_code": response.status_code},
                )
            # A media file is copied byte for byte. A compressed body would be expanded by
            # httpx as the caller reads it, unbounded, and would no longer be the file.
            _refuse_encoded_body(response, command=None)

            # An exception raised while the caller iterates the body is thrown back in at
            # this yield, so it is mapped here too rather than escaping as an httpx type.
            yield response
        except httpx.TimeoutException as exc:
            raise OperationTimeoutError(
                "The download stalled and timed out before it finished.",
                backend=BACKEND,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise NotConnectedError(
                "The download was interrupted before it finished. Whatever was written is "
                "incomplete and must be discarded; retrying is safe.",
                backend=BACKEND,
                retryable=True,
            ) from exc
        finally:
            await response.aclose()

    def _validated_url(self, url: str) -> str:
        """Confine a camera-supplied URL to the camera's own origin.

        Relative paths are accepted and resolved against the base URL. Absolute URLs must
        match the configured scheme, host and port exactly.
        """
        try:
            candidate = httpx.URL(url)
        except (httpx.InvalidURL, ValueError, TypeError) as exc:
            # The URL came from a device response, so a malformed one is malformed vendor
            # data -- `internal` in the taxonomy -- not a problem with the caller's request.
            # TypeError is included because a payload can supply a list, an object, a
            # number or null where a string belongs, and `httpx.URL` raises that rather
            # than ValueError for a non-string.
            raise InternalError(
                "The camera supplied a file URL that could not be parsed.",
                backend=BACKEND,
            ) from exc

        # A relative path is resolved against the camera's own address. A network-path
        # reference such as `//other.host/x.jpg` has no scheme but does name a host; httpx
        # would quietly keep only its path and fetch a different file from the camera than
        # the one the URL names. It is checked like an absolute URL instead, and refused.
        if not candidate.is_absolute_url and not candidate.host:
            return url

        base = httpx.URL(self._base_url)
        same_origin = (
            candidate.scheme == base.scheme
            and candidate.host == base.host
            and candidate.port == base.port
        )
        if not same_origin:
            raise InternalError(
                "The camera supplied a file URL pointing somewhere other than the camera "
                "itself. SphereLoom refuses to follow it.",
                backend=BACKEND,
                details={"expected_host": base.host, "received_host": candidate.host},
            )
        return url

    # ------------------------------------------------------------------ internals

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        retry_server_errors: bool,
        command: str | None = None,
        before_attempt: Callable[[], Awaitable[None]] | None = None,
        vendor_errors_to_caller: bool = False,
    ) -> dict[str, Any]:
        response, body = await self._send_with_retries(
            method,
            path,
            json=json,
            retry_server_errors=retry_server_errors,
            command=command,
            before_attempt=before_attempt,
        )
        payload = self._parse_json(response, body, command=command)
        # A 4xx carries the vendor's error envelope. `execute` and `command_status` hand it
        # back for the command runner to map with the command's context; every other
        # endpoint maps it here, or `info()` would cache an error as the camera's identity.
        if response.status_code >= 400 and not vendor_errors_to_caller:
            raise map_vendor_error(payload, command=command)
        return payload

    async def _send_once(
        self, method: str, path: str, *, json: Any, command: str | None
    ) -> tuple[httpx.Response, bytes]:
        """Send one request and read a bounded body.

        Streamed rather than buffered by httpx so the size limit applies while reading,
        not after the whole body is already in memory.
        """
        request = self._client.build_request(method, path, json=json)
        response = await self._client.send(request, stream=True)
        try:
            body = await self._read_bounded(response, command=command)
        finally:
            await response.aclose()
        return response, body

    async def _read_bounded(self, response: httpx.Response, *, command: str | None) -> bytes:
        _refuse_encoded_body(response, command=command)

        def too_large() -> InternalError:
            return InternalError(
                "The camera sent a response larger than SphereLoom will read. This is "
                "not a response any documented command produces.",
                backend=BACKEND,
                details={"limit_bytes": MAX_RESPONSE_BYTES, "command": command},
            )

        # An in-process transport can hand over a body that is already in memory, with
        # nothing left to stream. After the encoding check above it is the raw body, and
        # the same limit applies to it.
        if response.is_stream_consumed:
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise too_large()
            return response.content

        chunks: list[bytes] = []
        total = 0
        # Raw bytes, so the limit counts what arrived on the wire rather than what a
        # decoder made of it.
        async for chunk in response.aiter_raw():
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise too_large()
            chunks.append(chunk)
        return b"".join(chunks)

    async def _send_with_retries(
        self,
        method: str,
        path: str,
        *,
        retry_server_errors: bool,
        json: Any,
        command: str | None,
        before_attempt: Callable[[], Awaitable[None]] | None = None,
    ) -> tuple[httpx.Response, bytes]:
        """Send a request, repeating only the failures that are safe to repeat.

        Two budgets, not one. A setup failure never reached the camera, so it is repeated
        for every command including a capture. A completed 5xx did reach the camera, so it
        is repeated only when the command has no side effect. Conflating the two meant a
        capture gave up on the first connection error, contradicting this module's own
        contract.
        """
        last_error: Exception | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            # Runs before every attempt, retries included, so a throttled endpoint cannot
            # have several requests slipped into one window by a burst of retries.
            if before_attempt is not None:
                await before_attempt()

            try:
                response, body = await self._send_once(method, path, json=json, command=command)
            except (httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ConnectError) as exc:
                # Nothing reached the camera, so repeating is safe whatever the command is.
                last_error = exc
                if attempt >= MAX_ATTEMPTS:
                    raise NotConnectedError(
                        "Could not reach the camera. Check that this machine has joined the "
                        "camera's Wi-Fi access point and that the camera is powered on.",
                        backend=BACKEND,
                    ) from exc
            except httpx.TimeoutException as exc:
                # A read or write timeout is ambiguous: the request was sent, and the
                # camera may still be executing it. Retrying would put a second command in
                # flight while the first runs, breaking the vendor's one-at-a-time rule.
                #
                # Whether an *agent* may safely repeat it depends on the command: repeating
                # a listing is harmless, repeating a capture or a delete is not. Saying
                # "retryable" for the unsafe case would cause exactly the duplicate side
                # effect this policy exists to prevent, only one layer further out.
                raise OperationTimeoutError(
                    f"The camera did not respond within the timeout while calling "
                    f"{command or path}. The request was already sent, so it may still be "
                    "running on the camera; SphereLoom will not repeat it automatically.",
                    backend=BACKEND,
                    details={"command": command},
                    retryable=_is_safe_to_repeat(command),
                ) from exc
            except httpx.HTTPError as exc:
                # Also post-send and therefore ambiguous.
                raise NotConnectedError(
                    "Lost contact with the camera after the request was sent. It may still "
                    "have been acted on, so SphereLoom will not repeat it automatically.",
                    backend=BACKEND,
                    details={"command": command},
                    retryable=_is_safe_to_repeat(command),
                ) from exc
            else:
                should_retry = (
                    retry_server_errors
                    and response.status_code in _RETRYABLE_STATUS
                    and attempt < MAX_ATTEMPTS
                )
                if should_retry:
                    await self._backoff(attempt)
                    continue
                return response, body

            await self._backoff(attempt)

        # Unreachable: the loop either returns or raises on its final attempt.
        raise InternalError(  # pragma: no cover
            "The request loop exited without a result.", backend=BACKEND
        ) from last_error

    async def _backoff(self, attempt: int) -> None:
        """Wait before retrying, with jitter so concurrent callers do not resynchronise."""
        delay = min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), BACKOFF_MAX_SECONDS)
        await asyncio.sleep(delay * (0.5 + random.random() / 2))  # noqa: S311

    def _parse_json(
        self, response: httpx.Response, body: bytes, *, command: str | None
    ) -> dict[str, Any]:
        if response.status_code == 429:
            raise RateLimitedError(
                "The camera is rejecting requests as too frequent. Slow down and retry.",
                backend=BACKEND,
            )

        if response.status_code >= 500:
            raise NotConnectedError(
                f"The camera returned HTTP {response.status_code} while calling "
                f"{command or response.request.url.path}. It may be restarting or busy.",
                backend=BACKEND,
                details={"status_code": response.status_code},
                # A 5xx does not prove the camera did nothing before failing, so a
                # state-changing command must not be advertised as safe to repeat.
                retryable=_is_safe_to_repeat(command),
            )

        # 4xx carries the vendor's own error envelope, which the caller maps to the
        # taxonomy with far better messages than a status code alone. Anything else outside
        # 2xx is not a vendor answer at all: redirects are not followed, so a 3xx body is
        # not an OSC response, and accepting it would treat a redirect page as a result.
        # Not retryable, for any command: a redirect comes back the same way every time.
        if not response.is_success and response.status_code < 400:
            raise InternalError(
                f"The camera answered HTTP {response.status_code} instead of a result. "
                "Redirects are not followed, so this is not a response SphereLoom can use.",
                backend=BACKEND,
                details={"status_code": response.status_code, "command": command},
            )

        try:
            payload = strict_json_loads(body)
        except (ValueError, RecursionError) as exc:
            # Malformed JSON appears on some firmware under load. Deeply nested input
            # raises RecursionError rather than ValueError, and Python's decoder accepts
            # NaN and Infinity by default, neither of which is valid JSON or a value any
            # documented command returns. All three are the same thing to a caller: a
            # response that cannot be used.
            raise InternalError(
                _malformed_message(command),
                backend=BACKEND,
                details={
                    # Sliced before decoding: decoding first would materialise the whole
                    # untrusted body to produce two hundred characters.
                    "excerpt": body[:200].decode("utf-8", errors="replace"),
                    "command": command,
                },
                retryable=_is_safe_to_repeat(command),
            ) from exc

        if not isinstance(payload, dict):
            raise InternalError(
                "The camera returned a JSON value where an object was expected.",
                backend=BACKEND,
                # The type, not the value. Rendering an arbitrarily large list or nested
                # structure to produce a short excerpt recreates the allocation hazard the
                # bounded read exists to avoid.
                details={"received_type": type(payload).__name__, "command": command},
                retryable=_is_safe_to_repeat(command),
            )

        return payload


def _refuse_encoded_body(response: httpx.Response, *, command: str | None) -> None:
    """Refuse a body with any content encoding other than identity, before reading it.

    The client asks for identity encoding, and httpx decompresses whatever arrives before
    yielding a chunk, so a compressed body can expand far beyond a size limit before the
    limit sees it. A camera that compresses anyway is answering a request nobody made.
    """
    encoding = response.headers.get("content-encoding", "").strip().lower()
    if encoding not in {"", "identity"}:
        raise InternalError(
            "The camera sent a compressed response although SphereLoom asked for an "
            "uncompressed one. It is refused rather than decompressed into memory.",
            backend=BACKEND,
            details={"content_encoding": bounded_text(encoding, 32), "command": command},
        )


def _malformed_message(command: str | None) -> str:
    """Describe a malformed response, without recommending a retry that may duplicate.

    Malformed JSON after a capture leaves the outcome unknown: the camera may have acted.
    Telling the caller "retrying usually succeeds" would be advice to duplicate it.
    """
    base = (
        "The camera returned a response that is not valid JSON. This has been seen on some "
        "firmware revisions under load"
    )
    if _is_safe_to_repeat(command):
        return f"{base}; retrying usually succeeds."
    return (
        f"{base}. Because {command} may already have taken effect, check the camera state "
        "before deciding whether to repeat it."
    )
