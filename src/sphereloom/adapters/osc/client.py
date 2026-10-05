"""The HTTP client that talks to a camera over its access point.

Three constraints from the vendor's documentation shape this file, and each is enforced
here rather than left to callers to remember:

1. A static `X-XSRF-Protected: 1` header is required on every request. There is no other
   authentication; possession of the Wi-Fi credentials is the only gate.
2. Never send a command before the previous one has responded. A single lock enforces it,
   because "please remember to await" is not a safety mechanism.
3. `/osc/info` should not be polled more than once per second. A small cache enforces that
   and reports the age of what it returns, so a caller can tell fresh from remembered.

Retries are deliberately narrow. A dropped connection while listing files is worth
retrying; a dropped connection after `takePicture` is not, because the camera may well have
taken the picture. That distinction is the whole retry policy.
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
from sphereloom.domain.clock import Monotonic, SystemMonotonic
from sphereloom.domain.errors import (
    InternalError,
    InvalidArgumentError,
    NotConnectedError,
    OperationTimeoutError,
    RateLimitedError,
)
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

_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class CachedInfo:
    """A remembered `/osc/info` response and how stale it is."""

    payload: dict[str, Any]
    age_seconds: float


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
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._monotonic = monotonic or SystemMonotonic()
        # The client is constructed here rather than injected. Accepting an outside client
        # would let a caller bypass every protocol setting below -- the mandatory header,
        # the explicit timeouts, the redirect policy -- while this class still claimed to
        # enforce them. A configuration guarantee that can be opted out of silently is not
        # a guarantee.
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                XSRF_HEADER: XSRF_VALUE,
                "Content-Type": "application/json;charset=utf-8",
                "Accept": "application/json",
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
        self._info_attempted_at: float | None = None

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
                "GET", INFO_PATH, retryable=True, before_attempt=self._await_info_window
            )
            self._info_cache = payload
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
        if self._info_cache is None or self._info_attempted_at is None:
            return None
        age = self._monotonic.elapsed() - self._info_attempted_at
        if age >= INFO_MIN_INTERVAL_SECONDS:
            return None
        return CachedInfo(payload=self._info_cache, age_seconds=age)

    async def state(self) -> dict[str, Any]:
        """Read battery, storage and capture state."""
        return await self._request_json("POST", STATE_PATH, retryable=True)

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
                retryable=name in RETRY_SAFE_COMMANDS,
                command=name,
            )

    async def command_status(self, command_id: str) -> dict[str, Any]:
        """Poll a previously accepted command.

        Not taken under the command lock: polling is a read, and holding the lock while
        waiting for a capture would block every other call for the duration of the capture.
        """
        return await self._request_json(
            "POST", STATUS_PATH, json={"id": command_id}, retryable=True
        )

    @asynccontextmanager
    async def stream(self, url: str) -> AsyncIterator[httpx.Response]:
        """Open a streaming download of a file the camera advertised.

        The URL is validated against the configured camera origin first. It arrives in a
        device response, and a malformed or hostile payload could otherwise point anywhere;
        following it would send this client's requests, and its headers, to a host the
        operator never chose.

        Separate from the JSON path because media files are routinely gigabytes: they are
        never buffered, parsed, or logged.
        """
        target = self._validated_url(url)
        request = self._client.build_request("GET", target)
        response = await self._client.send(request, stream=True)
        try:
            response.raise_for_status()
            yield response
        finally:
            await response.aclose()

    def _validated_url(self, url: str) -> str:
        """Confine a camera-supplied URL to the camera's own origin.

        Relative paths are accepted and resolved against the base URL. Absolute URLs must
        match the configured scheme, host and port exactly.
        """
        candidate = httpx.URL(url)

        if not candidate.is_absolute_url:
            return url

        base = httpx.URL(self._base_url)
        same_origin = (
            candidate.scheme == base.scheme
            and candidate.host == base.host
            and candidate.port == base.port
        )
        if not same_origin:
            raise InvalidArgumentError(
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
        retryable: bool,
        command: str | None = None,
        before_attempt: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        response = await self._send_with_retries(
            method,
            path,
            json=json,
            retryable=retryable,
            command=command,
            before_attempt=before_attempt,
        )
        return self._parse_json(response, command=command)

    async def _send_with_retries(
        self,
        method: str,
        path: str,
        *,
        json: Any,
        retryable: bool,
        command: str | None,
        before_attempt: Callable[[], Awaitable[None]] | None = None,
    ) -> httpx.Response:
        attempts = MAX_ATTEMPTS if retryable else 1
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            # Runs before every attempt, retries included, so a throttled endpoint cannot
            # have several requests slipped into one window by a burst of retries.
            if before_attempt is not None:
                await before_attempt()

            try:
                response = await self._client.request(method, path, json=json)
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt >= attempts:
                    raise OperationTimeoutError(
                        f"The camera did not respond within the timeout while calling "
                        f"{command or path}. It may be busy, asleep, or out of Wi-Fi range.",
                        backend=BACKEND,
                    ) from exc
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= attempts:
                    raise NotConnectedError(
                        "Could not reach the camera. Check that this machine has joined the "
                        "camera's Wi-Fi access point and that the camera is powered on.",
                        backend=BACKEND,
                    ) from exc
            else:
                if response.status_code in _RETRYABLE_STATUS and attempt < attempts:
                    await self._backoff(attempt)
                    continue
                return response

            await self._backoff(attempt)

        # Unreachable: the loop either returns or raises on its final attempt.
        raise InternalError(  # pragma: no cover
            "The request loop exited without a result.", backend=BACKEND
        ) from last_error

    async def _backoff(self, attempt: int) -> None:
        """Wait before retrying, with jitter so concurrent callers do not resynchronise."""
        delay = min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), BACKOFF_MAX_SECONDS)
        await asyncio.sleep(delay * (0.5 + random.random() / 2))  # noqa: S311

    def _parse_json(self, response: httpx.Response, *, command: str | None) -> dict[str, Any]:
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
            )

        try:
            payload = response.json()
        except ValueError as exc:
            # Some firmware returns malformed JSON under load. Treating it as a crash would
            # be unhelpful; reporting it with a bounded excerpt is diagnosable.
            raise InternalError(
                "The camera returned a response that is not valid JSON. This has been seen "
                "on some firmware revisions under load; retrying usually succeeds.",
                backend=BACKEND,
                details={"excerpt": response.text[:200], "command": command},
            ) from exc

        if not isinstance(payload, dict):
            raise InternalError(
                "The camera returned a JSON value where an object was expected.",
                backend=BACKEND,
                details={"excerpt": str(payload)[:200], "command": command},
            )

        return payload
