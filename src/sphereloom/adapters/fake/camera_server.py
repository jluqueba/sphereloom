"""A protocol-faithful fake camera served over real HTTP on loopback.

Why a real HTTP server rather than a mocked client
--------------------------------------------------

Mocking the HTTP client would test the adapter against our own assumptions. Serving real
HTTP exercises the parts that actually break in the field: header handling, timeouts,
streaming, connection reuse, and partial transfers. The adapter cannot tell the difference
between this and a camera on the other end of a Wi-Fi link, which is the point.

Fidelity
--------

Response shapes follow the vendor's published protocol documentation: battery is a fraction
between zero and one, captures complete asynchronously through a polled command identifier,
and vendor-specific fields keep their underscore prefix. Where a real camera's behaviour is
known but undocumented, the code says so rather than inventing a detail.

This fake also ships as SphereLoom's demo backend, so anyone can try the server without
owning a camera.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from starlette.applications import Starlette
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from sphereloom.adapters.fake.scenarios import HEALTHY, Scenario
from sphereloom.domain.payloads import bounded_text, strict_json_loads

#: A genuine one-pixel JPEG. Downloads therefore produce a file that actually opens, which
#: matters when verifying that a transfer was byte-exact rather than merely the right size.
_TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
    "AQAAAAAAAAAAAAAAAAAAAAn/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAA/AKp//9k="
)

#: A valid MP4 file-type box followed by filler. Deliberately not a playable video: tests
#: care about transfer integrity, and generating real footage would bloat the repository.
_STUB_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 2048

DEFAULT_MODEL = "Insta360 X5"
DEFAULT_FIRMWARE = "v1.0.0-fake"
DEFAULT_SERIAL = "FAKE0000000000"
STORAGE_ROOT = "/DCIM/Camera01"

#: The vendor requires this static header on every protocol request. It is the only access
#: control the protocol has, and the fake enforces it so that an adapter which forgets it
#: fails here rather than against someone's camera.
XSRF_HEADER = "X-XSRF-Protected"
XSRF_VALUE = "1"

#: Values each writable option accepts. `getOptions` advertises these and `setOptions`
#: enforces them, because a fake that accepts a value the camera would reject teaches the
#: adapter a habit that breaks on hardware.
SUPPORTED_CAPTURE_MODES = ("image", "video")
SUPPORTED_PHOTO_STITCHING = ("none", "ondevice")
SUPPORTED_VIDEO_TYPES = ("normal", "timelapse", "hdr")
SUPPORTED_WHITE_BALANCE = ("auto", "daylight", "cloudy", "incandescent")
SUPPORTED_EXPOSURE_DELAY = (0, 3, 5, 10)
SUPPORTED_TOP_BOTTOM_CORRECTION = ("on", "off")

#: Options the vendor does not expose over this protocol. Named explicitly so the refusal
#: carries a useful message rather than looking like an unknown-option typo.
PROTOCOL_UNSUPPORTED_OPTIONS = frozenset({"iso", "shutterSpeed", "exposureProgram"})


def _is_accepted(value: Any, candidate: Any) -> bool:
    """Whether a supplied option value matches an accepted one, including its JSON type.

    Plain equality is not enough. In Python `False == 0` and `1 == True`, so a request
    sending `exposureDelay: false` would match the accepted value `0`, be stored unchanged,
    and then come back out of `getOptions` as a boolean where a number belongs. The fake
    would have blessed a malformed request that a real camera would reject.
    """
    if isinstance(value, bool) != isinstance(candidate, bool):
        return False
    return bool(value == candidate)


#: Largest request body the fake will read. A camera has finite memory; so should the thing
#: standing in for one. Without this a client can make the demo backend read without limit.
MAX_REQUEST_BYTES = 1024 * 1024


async def _request_object(request: Request) -> dict[str, Any] | None:
    """Parse a request body that must be a JSON object, or `None` if it is not.

    A real camera answers nonsense with a 400, not a stack trace. Three inputs would
    otherwise escape: a body that is valid JSON but not an object (`[1, 2]` parses, then
    `.get` raises `AttributeError`), one nested deeply enough that the decoder raises
    `RecursionError`, which is not a `ValueError` and so is not caught by the obvious
    `except` clause, and one simply too large to hold, since `request.json()` reads
    whatever arrives before anything gets to inspect it.
    """
    # One growing buffer rather than a list of chunks, so a client that sends its body a
    # byte at a time costs memory in proportion to the bytes, not to the number of reads.
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_REQUEST_BYTES:
            return None
        body.extend(chunk)

    try:
        payload = strict_json_loads(bytes(body))
    except (ValueError, RecursionError):
        return None
    return payload if isinstance(payload, dict) else None


def _malformed_request() -> JSONResponse:
    return JSONResponse(
        {"error": {"code": "invalidParameterValue", "message": "Malformed request."}},
        status_code=400,
    )


def _bounded_int(value: Any, *, maximum: int) -> int | None:
    """Coerce a client-supplied count, refusing anything that is not a sane number.

    `int()` on arbitrary input raises, which a real camera would not do, and an unbounded
    count would let one request ask the fake to build an arbitrarily large response.
    Booleans are refused explicitly because `True == 1` in Python, and non-finite floats
    before conversion, because `int(float("nan"))` raises rather than comparing false.
    Returns None when the value cannot be used, so the caller answers with a vendor error.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value != int(value) or value < 0:
        return None
    return min(int(value), maximum)


@dataclass(slots=True)
class FakeFile:
    """One file on the fake camera's card."""

    name: str
    kind: str
    captured_at: datetime
    content: bytes
    width: int = 0
    height: int = 0
    group_id: str | None = None

    @property
    def size(self) -> int:
        return len(self.content)

    @property
    def local_url(self) -> str:
        return f"{STORAGE_ROOT}/{self.name}"

    def file_url(self, base: str) -> str:
        return f"{base}{STORAGE_ROOT}/{self.name}"


@dataclass(slots=True)
class PendingCommand:
    """A capture the camera has accepted but not yet finished.

    Real captures are asynchronous: the command returns an identifier and the caller polls
    until the state becomes `done`. Completing after a fixed number of polls keeps tests
    deterministic without anyone sleeping.
    """

    command_id: str
    name: str
    polls_remaining: int
    results: dict[str, Any]


def _default_files() -> list[FakeFile]:
    """A small, plausible card: two photos and one two-file recording."""
    base = datetime(2026, 1, 15, 10, 30, tzinfo=UTC)
    return [
        FakeFile(
            name="IMG_20260115_103000_00_001.jpg",
            kind="image",
            captured_at=base,
            content=_TINY_JPEG,
            width=6080,
            height=3040,
        ),
        FakeFile(
            name="IMG_20260115_104500_00_002.jpg",
            kind="image",
            captured_at=base + timedelta(minutes=15),
            content=_TINY_JPEG,
            width=6080,
            height=3040,
        ),
        # One recording produces two files, one per lens. Anything that assumes a single
        # file per recording breaks on real hardware, so the default fixture includes it.
        FakeFile(
            name="VID_20260115_110000_00_003.mp4",
            kind="video",
            captured_at=base + timedelta(minutes=30),
            content=_STUB_MP4,
            width=5760,
            height=2880,
            group_id="003",
        ),
        FakeFile(
            name="VID_20260115_110000_10_003.mp4",
            kind="video",
            captured_at=base + timedelta(minutes=30),
            content=_STUB_MP4,
            width=5760,
            height=2880,
            group_id="003",
        ),
    ]


@dataclass
class FakeCameraState:
    """Everything the fake camera remembers."""

    model: str = DEFAULT_MODEL
    firmware_version: str = DEFAULT_FIRMWARE
    serial_number: str = DEFAULT_SERIAL
    battery_level: float = 0.78
    total_space: int = 63_900_000_000
    remaining_space: int = 41_200_000_000
    card_state: str = "pass"
    capture_mode: str = "image"
    photo_stitching: str = "ondevice"
    video_type: str = "normal"
    white_balance: str = "auto"
    exposure_delay: int = 0
    top_bottom_correction: str = "off"
    mute_enabled: bool = False
    recording_since: datetime | None = None
    files: list[FakeFile] = field(default_factory=_default_files)
    next_sequence: int = 100

    @property
    def is_recording(self) -> bool:
        return self.recording_since is not None


class FakeCamera:
    """The fake camera. Build an ASGI app with `build_app()`."""

    def __init__(
        self,
        *,
        scenario: Scenario = HEALTHY,
        state: FakeCameraState | None = None,
        capture_polls: int = 1,
    ) -> None:
        self.scenario = scenario
        self.state = FakeCameraState() if state is None else state
        self.capture_polls = capture_polls
        self._pending: dict[str, PendingCommand] = {}
        self._command_counter = 1000
        self._commands_failed = 0
        self._executing = False
        #: Requests the camera has seen, so tests can assert on protocol discipline such as
        #: the vendor's "one command in flight" guidance.
        self.request_log: list[str] = []
        #: Set when a second command arrives while one is still executing.
        self.concurrent_command_detected = False
        #: Signalled once a capture has been accepted. A test that needs to act *while* a
        #: capture is running can wait on this instead of guessing with a sleep, which
        #: would pass under favourable scheduling even if the behaviour were wrong.
        #: threading rather than asyncio, because the server runs in its own event loop.
        self.capture_accepted = threading.Event()

    # ------------------------------------------------------------------ helpers

    def _next_command_id(self) -> str:
        self._command_counter += 1
        return f"{self._command_counter:06d}"

    def _next_file_sequence(self) -> int:
        self.state.next_sequence += 1
        return self.state.next_sequence

    def _base_url(self, request: Request) -> str:
        return f"{request.url.scheme}://{request.url.netloc}"

    async def _apply_latency(self) -> None:
        if self.scenario.latency_seconds > 0:
            await asyncio.sleep(self.scenario.latency_seconds)

    def _vendor_error(self, name: str, code: str, message: str) -> JSONResponse:
        """Render an error the way the vendor documents it."""
        return JSONResponse(
            {"name": name, "state": "error", "error": {"code": code, "message": message}}
        )

    def _path_failure(self, request: Request) -> Response | None:
        """Apply a scenario that targets this specific endpoint."""
        if request.url.path in self.scenario.failing_paths:
            return Response("upstream failure", status_code=503)
        return None

    def _guard(self, request: Request) -> Response | None:
        """Checks every OSC protocol endpoint must pass. Returns a rejection, or None.

        Centralised on purpose: when the header check lived in one handler it silently did
        not apply to the others, and a selective failure scenario only affected the single
        endpoint that happened to implement it.

        File downloads deliberately do not go through here. The vendor documents them as
        plain HTTP requests to a file URL, with no protocol header, and inventing a
        requirement the camera does not have would be its own kind of infidelity.
        """
        if request.headers.get(XSRF_HEADER) != XSRF_VALUE:
            return JSONResponse(
                {
                    "error": {
                        "code": "invalidParameterValue",
                        "message": f"Missing or invalid {XSRF_HEADER} header.",
                    }
                },
                status_code=403,
            )

        if self.scenario.redirect_responses:
            return JSONResponse(
                {"name": "redirected"},
                status_code=302,
                headers={"Location": "/elsewhere"},
            )

        if self.scenario.non_finite_json:
            return Response(
                '{"fingerprint": "FPR", "state": {"batteryLevel": NaN}}',
                media_type="application/json",
            )

        if self.scenario.overflowing_number:
            return Response(
                '{"fingerprint": "FPR", "state": {"batteryLevel": 1e400}}',
                media_type="application/json",
            )

        if self.scenario.deeply_nested_json:
            depth = 20_000
            return Response(
                "[" * depth + "]" * depth,
                media_type="application/json",
            )

        if self.scenario.array_payload:
            return Response(
                json.dumps(["not", "an", "object"]),
                media_type="application/json",
            )

        if self.scenario.oversized_responses:
            # Streamed so the fake does not have to hold the whole thing either.
            async def flood() -> AsyncIterator[bytes]:
                block = b'{"padding": "' + b"x" * 65_536
                for _ in range(200):
                    yield block

            return StreamingResponse(flood(), media_type="application/json")

        return self._path_failure(request)

    def _should_fail_command(self) -> bool:
        if self._commands_failed < self.scenario.fail_first_n_commands:
            self._commands_failed += 1
            return True
        return False

    # ------------------------------------------------------------------ endpoints

    async def osc_info(self, request: Request) -> Response:
        self.request_log.append("GET /osc/info")
        await self._apply_latency()

        rejected = self._guard(request)
        if rejected is not None:
            return rejected

        return JSONResponse(
            {
                "manufacturer": "Arashi Vision",
                "model": self.state.model,
                "serialNumber": self.state.serial_number,
                "firmwareVersion": self.state.firmware_version,
                "supportUrl": "https://www.insta360.com/",
                "endpoints": {"httpPort": 80, "httpUpdatesPort": 80},
                "gps": False,
                "gyro": True,
                "uptime": 480,
                "api": [
                    "/osc/info",
                    "/osc/state",
                    "/osc/checkForUpdates",
                    "/osc/commands/execute",
                    "/osc/commands/status",
                ],
                "apiLevel": [2],
                "_sensorModuleType": "Dual_Fisheye",
                "_vendorVersion": "v1.0_fake",
            }
        )

    async def osc_state(self, request: Request) -> Response:
        self.request_log.append("POST /osc/state")
        await self._apply_latency()

        rejected = self._guard(request)
        if rejected is not None:
            return rejected

        if self.scenario.malformed_json:
            return Response("{not valid json", media_type="application/json")

        card_state = "noSpace" if self.scenario.storage_full else self.state.card_state
        return JSONResponse(
            {
                "fingerprint": "FPR_FAKE_0001",
                "state": {
                    "_cardState": card_state,
                    "batteryLevel": self.state.battery_level,
                    "storageUri": f"{self._base_url(request)}{STORAGE_ROOT}/",
                    "_captureStatus": "shooting" if self.state.is_recording else "idle",
                },
            }
        )

    async def osc_execute(self, request: Request) -> Response:
        # Logged before anything can fail, so a request that times out mid-flight is still
        # counted. A test asserting "this was attempted once" needs that to be true even
        # when the client gave up before the body was read.
        self.request_log.append("POST /osc/commands/execute")

        rejected = self._guard(request)
        if rejected is not None:
            return rejected

        if self._executing:
            # The vendor explicitly advises against overlapping commands. Recording the
            # violation lets a test prove the adapter serialises its calls.
            self.concurrent_command_detected = True

        # The flag is set before the first await on purpose. Delaying it until after the
        # latency sleep would let two overlapping requests both wait, then dispatch one
        # after the other, so a missing serialising lock in the adapter would go unnoticed.
        self._executing = True
        try:
            await self._apply_latency()
            return await self._dispatch(request)
        except ClientDisconnect:
            # The caller timed out and went away. Nothing to answer, and nothing wrong.
            return Response(status_code=499)
        finally:
            self._executing = False

    async def _dispatch(self, request: Request) -> Response:
        payload = await _request_object(request)
        if payload is None:
            return _malformed_request()

        name = str(payload.get("name", ""))
        parameters = payload.get("parameters")
        if parameters is None:
            parameters = {}
        elif not isinstance(parameters, dict):
            return JSONResponse(
                {
                    "error": {
                        "code": "invalidParameterValue",
                        "message": "parameters must be an object.",
                    }
                },
                status_code=400,
            )
        self.request_log.append(f"POST /osc/commands/execute {name}")

        if self.scenario.server_error:
            return Response("Internal Server Error", status_code=500)

        if self.scenario.malformed_json:
            return Response('{"name": "' + name + '", "state": don', media_type="application/json")

        if self._should_fail_command():
            return Response("Service Unavailable", status_code=503)

        if self.scenario.unactivated:
            return self._vendor_error(
                name,
                "unactivated",
                "Please activate your camera in the vendor's official app.",
            )

        if self.scenario.busy:
            return self._vendor_error(
                name, "disabledCommand", "Another capture is currently running."
            )

        handlers = {
            "camera.getOptions": self._get_options,
            "camera.setOptions": self._set_options,
            "camera.takePicture": self._take_picture,
            "camera.startCapture": self._start_capture,
            "camera.stopCapture": self._stop_capture,
            "camera.listFiles": self._list_files,
            "camera.delete": self._delete,
        }
        handler = handlers.get(name)
        if handler is None:
            return self._vendor_error(
                name, "unknownCommand", f"Unknown command {bounded_text(name, 64)}."
            )

        return handler(request, parameters)

    async def osc_status(self, request: Request) -> Response:
        self.request_log.append("POST /osc/commands/status")
        await self._apply_latency()

        rejected = self._guard(request)
        if rejected is not None:
            return rejected

        payload = await _request_object(request)
        if payload is None:
            return _malformed_request()

        command_id = str(payload.get("id", ""))
        pending = self._pending.get(command_id)
        if pending is None:
            return JSONResponse(
                {
                    "error": {
                        "code": "invalidParameterValue",
                        "message": f"Unknown command id {bounded_text(command_id, 64)}.",
                    }
                },
                status_code=400,
            )

        if pending.polls_remaining > 0:
            pending.polls_remaining -= 1
            completion = 1.0 - (pending.polls_remaining / max(self.capture_polls, 1))
            return JSONResponse(
                {
                    "name": pending.name,
                    "state": "inProgress",
                    "id": pending.command_id,
                    "progress": {"completion": round(completion, 2)},
                }
            )

        del self._pending[command_id]
        return JSONResponse({"name": pending.name, "state": "done", "results": pending.results})

    async def osc_download(self, request: Request) -> Response:
        name = request.path_params["filename"]
        self.request_log.append(f"GET {STORAGE_ROOT}/{name}")
        await self._apply_latency()

        rejected = self._path_failure(request)
        if rejected is not None:
            return rejected

        # A redirect a client is not allowed to follow. Real firmware has been known to
        # answer this way, and a downloader that trusted the body would write the redirect
        # page to disk under a media filename.
        if name == "redirect-me.jpg":
            return Response(status_code=302, headers={"Location": "/elsewhere.jpg"})

        match = next((f for f in self.state.files if f.name == name), None)
        if match is None:
            return Response("Not Found", status_code=404)

        content = match.content
        media_type = "image/jpeg" if match.kind == "image" else "video/mp4"

        if self.scenario.truncate_downloads:
            # Declare the full length, send half, then abort. A server cannot politely
            # under-deliver on Content-Length: the HTTP layer refuses. Real truncation
            # therefore always arrives as a broken connection, which is the lesson the
            # download worker has to learn -- the transport will not tell you the file is
            # incomplete, so the byte count must be verified.
            async def truncated() -> AsyncIterator[bytes]:
                yield content[: len(content) // 2]
                message = "connection lost after a partial transfer"
                raise ConnectionResetError(message)

            return StreamingResponse(
                truncated(),
                media_type=media_type,
                headers={"Content-Length": str(len(content))},
            )

        if self.scenario.stall_downloads_after_headers:
            # Headers and one chunk arrive, then nothing. The caller is inside the body
            # iteration when its read timeout fires, which is a different code path from a
            # response that is merely slow to start.
            async def stalled() -> AsyncIterator[bytes]:
                yield content[:1]
                await asyncio.sleep(3600)

            return StreamingResponse(stalled(), media_type=media_type)

        if self.scenario.drop_downloads:
            # The same failure, earlier in the transfer.
            async def dropped() -> AsyncIterator[bytes]:
                yield content[: len(content) // 3]
                message = "connection dropped mid-transfer"
                raise ConnectionResetError(message)

            return StreamingResponse(
                dropped(),
                media_type=media_type,
                headers={"Content-Length": str(len(content))},
            )

        return Response(content, media_type=media_type)

    # ------------------------------------------------------------------ commands

    def _get_options(self, request: Request, parameters: dict[str, Any]) -> Response:
        requested = parameters.get("optionNames")
        if requested is None:
            requested = []
        elif not isinstance(requested, list):
            return self._vendor_error(
                "camera.getOptions", "invalidParameterValue", "optionNames must be a list."
            )
        elif not all(isinstance(name, str) for name in requested):
            # Checking only that it is a list still lets `[{"x": 1}]` through, and a
            # dictionary is unhashable, so the membership test below would raise TypeError
            # and the fake would answer 500 instead of the vendor error it means to.
            return self._vendor_error(
                "camera.getOptions",
                "invalidParameterValue",
                "optionNames must contain only strings.",
            )
        available: dict[str, Any] = {
            "captureMode": self.state.capture_mode,
            "captureModeSupport": list(SUPPORTED_CAPTURE_MODES),
            "photoStitching": self.state.photo_stitching,
            "photoStitchingSupport": list(SUPPORTED_PHOTO_STITCHING),
            "totalSpace": self.state.total_space,
            "remainingSpace": 0 if self.scenario.storage_full else self.state.remaining_space,
            "exposureDelay": self.state.exposure_delay,
            "exposureDelaySupport": list(SUPPORTED_EXPOSURE_DELAY),
            "whiteBalance": self.state.white_balance,
            "whiteBalanceSupport": list(SUPPORTED_WHITE_BALANCE),
            "fileFormat": {"type": "jpeg", "width": 6080, "height": 3040},
            "_videoType": self.state.video_type,
            "_videoTypeSupport": list(SUPPORTED_VIDEO_TYPES),
            "_topBottomCorrection": self.state.top_bottom_correction,
            "_MuteEnable": self.state.mute_enabled,
            "_batteryCapacity": int(self.state.battery_level * 100),
            "_sysTimestamp": 1767225600,
        }
        selected = (
            {name: available[name] for name in requested if name in available}
            if requested
            else available
        )
        return JSONResponse(
            {"name": "camera.getOptions", "state": "done", "results": {"options": selected}}
        )

    #: Writable options, mapped to the state attribute they set and the values they accept.
    #: Driving both validation and assignment from one table is what stops `getOptions` and
    #: `setOptions` drifting apart, which is the bug this replaced.
    _WRITABLE_OPTIONS: ClassVar[dict[str, tuple[str, tuple[Any, ...]]]] = {
        "captureMode": ("capture_mode", SUPPORTED_CAPTURE_MODES),
        "photoStitching": ("photo_stitching", SUPPORTED_PHOTO_STITCHING),
        "whiteBalance": ("white_balance", SUPPORTED_WHITE_BALANCE),
        "exposureDelay": ("exposure_delay", SUPPORTED_EXPOSURE_DELAY),
        "_videoType": ("video_type", SUPPORTED_VIDEO_TYPES),
        "_topBottomCorrection": ("top_bottom_correction", SUPPORTED_TOP_BOTTOM_CORRECTION),
        "_MuteEnable": ("mute_enabled", (True, False)),
    }

    def _set_options(self, request: Request, parameters: dict[str, Any]) -> Response:
        options = parameters.get("options")
        if options is None:
            options = {}
        elif not isinstance(options, dict):
            return self._vendor_error(
                "camera.setOptions", "invalidParameterValue", "options must be an object."
            )

        # The vendor documents no exposure control over this protocol. Rejecting it here
        # keeps the fake honest: if the adapter ever claims to support it, a test fails.
        unsupported = PROTOCOL_UNSUPPORTED_OPTIONS & set(options)
        if unsupported:
            return self._vendor_error(
                "camera.setOptions",
                "invalidParameterName",
                f"Option(s) not supported over this protocol: {', '.join(sorted(unsupported))}.",
            )

        unknown = set(options) - set(self._WRITABLE_OPTIONS)
        if unknown:
            return self._vendor_error(
                "camera.setOptions",
                "invalidParameterName",
                f"Unknown or read-only option(s): {', '.join(sorted(unknown))}.",
            )

        # Validate everything before changing anything. A half-applied settings change is
        # worse than a rejected one, because the caller cannot tell what state it left.
        for name, value in options.items():
            _, allowed = self._WRITABLE_OPTIONS[name]
            if not any(_is_accepted(value, candidate) for candidate in allowed):
                return self._vendor_error(
                    "camera.setOptions",
                    "invalidParameterValue",
                    f"{bounded_text(value, 64)} is not an accepted value for "
                    f"{bounded_text(name, 64)}.",
                )

        for name, value in options.items():
            attribute, _ = self._WRITABLE_OPTIONS[name]
            setattr(self.state, attribute, value)

        return JSONResponse({"name": "camera.setOptions", "state": "done"})

    def _take_picture(self, request: Request, parameters: dict[str, Any]) -> Response:
        if self.state.capture_mode != "image":
            return self._vendor_error(
                "camera.takePicture",
                "disabledCommand",
                "Currently camera is not working in image mode",
            )
        if self.scenario.storage_full:
            return self._vendor_error(
                "camera.takePicture", "noFreeSpace", "There is no free space on the card."
            )

        sequence = self._next_file_sequence()
        captured = FakeFile(
            name=f"IMG_20260115_120000_00_{sequence:03d}.jpg",
            kind="image",
            captured_at=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
            content=_TINY_JPEG,
            width=6080,
            height=3040,
        )
        self.state.files.append(captured)

        base = self._base_url(request)
        command_id = self._next_command_id()
        self._pending[command_id] = PendingCommand(
            command_id=command_id,
            name="camera.takePicture",
            polls_remaining=self.capture_polls,
            results={
                "fileUrl": captured.file_url(base),
                "_fileGroup": [captured.file_url(base)],
                "_localFileGroup": [captured.local_url],
            },
        )
        self.capture_accepted.set()
        return JSONResponse(
            {
                "name": "camera.takePicture",
                "state": "inProgress",
                "id": command_id,
                "progress": {"completion": 0},
            }
        )

    def _start_capture(self, request: Request, parameters: dict[str, Any]) -> Response:
        if self.state.capture_mode != "video":
            return self._vendor_error(
                "camera.startCapture",
                "disabledCommand",
                "Currently camera is not working in video mode",
            )
        if self.state.is_recording:
            return self._vendor_error(
                "camera.startCapture", "disabledCommand", "A recording is already running."
            )
        if self.scenario.storage_full:
            return self._vendor_error(
                "camera.startCapture", "noFreeSpace", "There is no free space on the card."
            )

        self.state.recording_since = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        return JSONResponse({"name": "camera.startCapture", "state": "done"})

    def _stop_capture(self, request: Request, parameters: dict[str, Any]) -> Response:
        if not self.state.is_recording:
            return self._vendor_error(
                "camera.stopCapture", "disabledCommand", "No recording is running."
            )

        self.state.recording_since = None
        sequence = self._next_file_sequence()
        group = f"{sequence:03d}"
        # A recording yields one file per lens. Returning both is what a real camera does.
        produced = [
            FakeFile(
                name=f"VID_20260115_120000_{lens}_{group}.mp4",
                kind="video",
                captured_at=datetime(2026, 1, 15, 12, 0, tzinfo=UTC),
                content=_STUB_MP4,
                width=5760,
                height=2880,
                group_id=group,
            )
            for lens in ("00", "10")
        ]
        self.state.files.extend(produced)

        base = self._base_url(request)
        return JSONResponse(
            {
                "name": "camera.stopCapture",
                "state": "done",
                "results": {
                    "fileUrls": [f.file_url(base) for f in produced],
                    "_localFileUrls": [f.local_url for f in produced],
                },
            }
        )

    def _list_files(self, request: Request, parameters: dict[str, Any]) -> Response:
        file_type = parameters.get("fileType", "all")
        if not isinstance(file_type, str):
            return self._vendor_error(
                "camera.listFiles", "invalidParameterValue", "fileType must be a string."
            )

        entry_count = _bounded_int(parameters.get("entryCount", 10), maximum=1000)
        if entry_count is None:
            return self._vendor_error(
                "camera.listFiles", "invalidParameterValue", "entryCount must be a number."
            )

        start_position = _bounded_int(parameters.get("startPosition", 0), maximum=100_000)
        if start_position is None:
            return self._vendor_error(
                "camera.listFiles",
                "invalidParameterValue",
                "startPosition must be a number.",
            )

        if file_type == "all":
            selected = list(self.state.files)
        else:
            selected = [f for f in self.state.files if f.kind == file_type]

        # Newest first, which is the order a gallery is useful in.
        selected.sort(key=lambda f: (f.captured_at, f.name), reverse=True)
        window = selected[start_position : start_position + entry_count]

        base = self._base_url(request)
        entries = [
            {
                "name": f.name,
                "fileUrl": f.file_url(base),
                "_localFileUrl": f.local_url,
                "size": f.size,
                "width": f.width,
                "height": f.height,
                "dateTimeZone": f.captured_at.strftime("%Y:%m:%d %H:%M:%S+00:00"),
                "isProcessed": True,
                "previewUrl": "",
            }
            for f in window
        ]
        return JSONResponse(
            {
                "name": "camera.listFiles",
                "state": "done",
                "results": {"entries": entries, "totalEntries": len(selected)},
            }
        )

    def _delete(self, request: Request, parameters: dict[str, Any]) -> Response:
        requested = parameters.get("fileUrls")
        if requested is None:
            requested = []
        elif not isinstance(requested, list):
            return self._vendor_error(
                "camera.delete", "invalidParameterValue", "fileUrls must be a list."
            )
        known = {f.name: f for f in self.state.files}

        missing: list[str] = []
        # Keyed by name so the same file listed twice is removed once. Without this the
        # second removal raises, returning a server error after the file is already gone:
        # a failed call that nevertheless changed state, which is the worst kind.
        removable: dict[str, FakeFile] = {}
        for url in requested:
            name = str(url).rsplit("/", 1)[-1]
            if name in known:
                removable[name] = known[name]
            else:
                missing.append(str(url))

        if missing:
            return JSONResponse(
                {
                    "error": {
                        "code": "invalidParameterValue",
                        "message": f"Parameter {missing[0]} doesn't exist.",
                    }
                }
            )

        for target in removable.values():
            self.state.files.remove(target)

        return JSONResponse({"name": "camera.delete", "state": "done", "results": {"fileUrls": []}})

    # ------------------------------------------------------------------ assembly

    def build_app(self) -> Starlette:
        """Build the ASGI application serving this camera."""
        return Starlette(
            routes=[
                Route("/osc/info", self.osc_info, methods=["GET"]),
                Route("/osc/state", self.osc_state, methods=["POST"]),
                Route("/osc/commands/execute", self.osc_execute, methods=["POST"]),
                Route("/osc/commands/status", self.osc_status, methods=["POST"]),
                Route(f"{STORAGE_ROOT}/{{filename}}", self.osc_download, methods=["GET"]),
            ]
        )

    def with_scenario(self, scenario: Scenario) -> FakeCamera:
        """Return a camera sharing this state but misbehaving differently."""
        return FakeCamera(
            scenario=replace(scenario), state=self.state, capture_polls=self.capture_polls
        )
