"""The OSC client and command runner, exercised against the fake camera over real HTTP.

These are component tests on purpose. The behaviours that matter here — the command lock,
the info cache, retry selectivity, asynchronous completion — only exist because of how a
real camera behaves, and mocking the transport would test our assumptions instead of the
protocol.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from sphereloom.adapters.fake import scenarios
from sphereloom.adapters.fake.camera_server import FakeCamera
from sphereloom.adapters.fake.runner import run_fake_camera
from sphereloom.adapters.osc.client import (
    INFO_MIN_INTERVAL_SECONDS,
    MAX_RESPONSE_BYTES,
    RETRY_SAFE_COMMANDS,
    OscHttpClient,
)
from sphereloom.adapters.osc.commands import CommandRunner
from sphereloom.domain.clock import FakeMonotonic
from sphereloom.domain.errors import (
    CameraBusyError,
    InternalError,
    InvalidArgumentError,
    NotConnectedError,
    NotFoundError,
    OperationTimeoutError,
    SphereLoomError,
    StorageFullError,
)


@pytest.fixture
def camera() -> FakeCamera:
    return FakeCamera()


@pytest.fixture
async def client(camera: FakeCamera) -> AsyncIterator[OscHttpClient]:
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        try:
            yield http
        finally:
            await http.aclose()


@pytest.fixture
def runner(client: OscHttpClient) -> CommandRunner:
    return CommandRunner(client, default_deadline=10.0)


# ---------------------------------------------------------------- transport basics


async def test_the_required_header_is_sent_on_every_request(client: OscHttpClient) -> None:
    """The fake rejects requests without it, so reaching a payload proves it was sent."""
    info = await client.info()

    assert info.payload["model"]


async def test_state_is_read(client: OscHttpClient) -> None:
    payload = await client.state()

    assert 0.0 <= payload["state"]["batteryLevel"] <= 1.0


async def test_an_unreachable_camera_says_so_plainly() -> None:
    """The most common failure by far is "you are not on the camera's Wi-Fi"."""
    http = OscHttpClient("http://127.0.0.1:9")
    try:
        with pytest.raises(NotConnectedError) as caught:
            await http.state()
    finally:
        await http.aclose()

    assert "wi-fi" in caught.value.message.lower()


async def test_malformed_json_is_reported_with_an_excerpt() -> None:
    """Some firmware returns broken JSON under load; a crash would be unhelpful."""
    camera = FakeCamera(scenario=scenarios.MALFORMED_JSON)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        try:
            with pytest.raises(InternalError) as caught:
                await http.state()
        finally:
            await http.aclose()

    assert "json" in caught.value.message.lower()
    assert caught.value.details["excerpt"]


# ---------------------------------------------------------------- info cache


async def test_info_is_cached_within_the_vendors_polling_window(
    camera: FakeCamera, client: OscHttpClient
) -> None:
    """The vendor asks for at most one info request per second, so we honour it."""
    await client.info()
    await client.info()
    await client.info()

    assert camera.request_log.count("GET /osc/info") == 1


async def test_cached_info_reports_its_age(client: OscHttpClient) -> None:
    """A caller deciding whether to trust a reading deserves to know how old it is."""
    first = await client.info()
    second = await client.info()

    assert first.age_seconds == 0.0
    assert second.age_seconds >= 0.0


async def test_the_cache_expires(camera: FakeCamera) -> None:
    monotonic = FakeMonotonic()
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url, monotonic=monotonic)
        try:
            await http.info()
            monotonic.advance(INFO_MIN_INTERVAL_SECONDS + 0.1)
            await http.info()
        finally:
            await http.aclose()

    assert camera.request_log.count("GET /osc/info") == 2


async def test_a_forced_refresh_waits_for_the_throttle_rather_than_skipping_it(
    camera: FakeCamera,
) -> None:
    """The throttle is a protocol constraint, not a performance optimisation.

    A forced refresh gets live data, but it waits its turn. The fake monotonic source is
    deliberately left *inside* the window so the waiting branch actually runs: advancing
    past the interval first would make `remaining` non-positive and test nothing.
    """
    monotonic = FakeMonotonic()
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url, monotonic=monotonic)
        try:
            await http.info()
            # Only part of the window has passed, so the refresh must wait out the rest.
            monotonic.advance(0.3)
            started = time.monotonic()
            await http.info(force_refresh=True)
            waited = time.monotonic() - started
        finally:
            await http.aclose()

    assert camera.request_log.count("GET /osc/info") == 2
    assert waited >= INFO_MIN_INTERVAL_SECONDS - 0.3 - 0.05, (
        "the forced refresh returned without waiting out the vendor window"
    )


async def test_concurrent_callers_cannot_burst_through_the_throttle(
    camera: FakeCamera,
) -> None:
    """Eight callers all missing an empty cache must not produce eight requests.

    A per-call check is not a throttle; only serialising the endpoint makes the guarantee
    hold under concurrency.
    """
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        try:
            await asyncio.gather(*(http.info() for _ in range(8)))
        finally:
            await http.aclose()

    assert camera.request_log.count("GET /osc/info") == 1


# ---------------------------------------------------------------- command discipline


async def test_commands_are_serialised(camera: FakeCamera, client: OscHttpClient) -> None:
    """The vendor advises never overlapping commands, and hope is not a mechanism.

    The fake flags any overlap it sees, so this proves the lock does its job under
    genuinely concurrent callers.
    """
    await asyncio.gather(*(client.execute("camera.getOptions") for _ in range(8)))

    assert camera.concurrent_command_detected is False


async def test_status_polling_does_not_hold_the_command_lock() -> None:
    """Holding the lock while awaiting a capture would stall every other call for its
    duration, making a status check impossible exactly when it is most wanted.

    Synchronised on the camera's own signal rather than a sleep: a fixed delay would let
    this pass under favourable scheduling even if polling did hold the lock.
    """
    slow = FakeCamera(capture_polls=4)
    with run_fake_camera(slow) as base_url:
        http = OscHttpClient(base_url)
        runner = CommandRunner(http, default_deadline=10.0)
        try:
            capture = asyncio.create_task(runner.run("camera.takePicture"))

            accepted = await asyncio.get_running_loop().run_in_executor(
                None, slow.capture_accepted.wait, 5.0
            )
            assert accepted, "the capture was never accepted by the camera"

            # This would block until the capture finished if the lock were held.
            options = await asyncio.wait_for(http.execute("camera.getOptions"), timeout=5.0)
            await capture
        finally:
            await http.aclose()

    assert options["state"] == "done"


# ---------------------------------------------------------------- retry policy


async def test_idempotent_requests_recover_from_a_transient_failure() -> None:
    """A flaky access point should not surface as an error for a read."""
    camera = FakeCamera(scenario=scenarios.FLAKY)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        try:
            payload = await http.execute("camera.getOptions")
        finally:
            await http.aclose()

    assert payload["state"] == "done"


async def test_a_capture_is_not_repeated_after_a_server_error() -> None:
    """A completed 5xx reached the camera, so repeating takePicture could take a second photo.

    The name is deliberately narrow. A capture *is* retried when the request provably never
    left the host, which is the separate setup-failure budget; claiming a capture is never
    retried would contradict a guarantee the suite requires elsewhere.
    """
    camera = FakeCamera(scenario=scenarios.FLAKY)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        try:
            with pytest.raises(NotConnectedError):
                await http.execute("camera.takePicture")
        finally:
            await http.aclose()

    assert camera.request_log.count("POST /osc/commands/execute") == 1


@pytest.mark.parametrize(
    "command",
    ["camera.takePicture", "camera.startCapture", "camera.stopCapture", "camera.delete"],
)
async def test_state_changing_commands_are_not_retry_safe(command: str) -> None:
    """The allowlist is the guarantee. Anything absent from it must not be repeated."""
    assert command not in RETRY_SAFE_COMMANDS


def test_the_retry_allowlist_excludes_anything_that_changes_state() -> None:
    """A command added later must not inherit retries by omission."""
    assert {"camera.getOptions", "camera.listFiles"} == RETRY_SAFE_COMMANDS


async def test_retries_are_bounded() -> None:
    """A camera that is simply down must fail promptly, not retry forever."""
    camera = FakeCamera(scenario=scenarios.SERVER_ERROR)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        try:
            with pytest.raises(NotConnectedError):
                await http.execute("camera.getOptions")
        finally:
            await http.aclose()

    executions = [entry for entry in camera.request_log if "getOptions" in entry]
    assert len(executions) <= 3


# ---------------------------------------------------------------- asynchronous completion


async def test_a_capture_is_awaited_to_completion(runner: CommandRunner) -> None:
    """The acknowledgement is not the result.

    Returning after the acknowledgement would hand back a photo that does not exist yet.
    """
    result = await runner.run("camera.takePicture")

    assert result.results["fileUrl"].endswith(".jpg")
    assert result.command_id is not None


async def test_a_slow_capture_is_still_awaited() -> None:
    slow = FakeCamera(capture_polls=5)
    with run_fake_camera(slow) as base_url:
        http = OscHttpClient(base_url)
        runner = CommandRunner(http, default_deadline=10.0)
        try:
            result = await runner.run("camera.takePicture")
        finally:
            await http.aclose()

    assert result.results["fileUrl"]


async def test_a_capture_that_never_finishes_times_out_and_names_the_command_id() -> None:
    """On timeout the camera may still be working, so the identifier must be reported."""
    stuck = FakeCamera(capture_polls=10_000)
    with run_fake_camera(stuck) as base_url:
        http = OscHttpClient(base_url)
        runner = CommandRunner(http, default_deadline=0.3)
        try:
            with pytest.raises(OperationTimeoutError) as caught:
                await runner.run("camera.takePicture")
        finally:
            await http.aclose()

    assert caught.value.details["command_id"]
    assert "command id" in caught.value.message.lower()


async def test_a_synchronous_command_returns_without_polling(
    camera: FakeCamera, runner: CommandRunner
) -> None:
    await runner.run("camera.getOptions")

    assert not any("status" in entry for entry in camera.request_log)


# ---------------------------------------------------------------- vendor errors


async def test_a_busy_camera_surfaces_as_camera_busy() -> None:
    camera = FakeCamera(scenario=scenarios.BUSY)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        runner = CommandRunner(http)
        try:
            with pytest.raises(CameraBusyError):
                await runner.run("camera.takePicture")
        finally:
            await http.aclose()


async def test_a_full_card_surfaces_as_storage_full() -> None:
    camera = FakeCamera(scenario=scenarios.STORAGE_FULL)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        runner = CommandRunner(http)
        try:
            with pytest.raises(StorageFullError):
                await runner.run("camera.takePicture")
        finally:
            await http.aclose()


async def test_a_wrong_mode_capture_reports_the_vendor_reason(runner: CommandRunner) -> None:
    """The camera's own words are preserved, because they are often the actionable part."""
    await runner.run("camera.setOptions", {"options": {"captureMode": "video"}})

    with pytest.raises(CameraBusyError) as caught:
        await runner.run("camera.takePicture")

    assert caught.value.reason is not None
    assert "image mode" in caught.value.reason


# ---------------------------------------------------------------- streaming


async def test_a_file_can_be_streamed_without_buffering(client: OscHttpClient) -> None:
    """Media files are routinely gigabytes and must never be materialised in memory."""
    listing = await client.execute("camera.listFiles", {"fileType": "image", "entryCount": 1})
    entry = listing["results"]["entries"][0]

    chunks = 0
    received = 0
    async with client.stream(entry["fileUrl"]) as response:
        async for chunk in response.aiter_bytes():
            chunks += 1
            received += len(chunk)

    assert received == entry["size"]


async def test_a_relative_file_path_is_accepted(client: OscHttpClient) -> None:
    listing = await client.execute("camera.listFiles", {"fileType": "image", "entryCount": 1})
    path = listing["results"]["entries"][0]["_localFileUrl"]

    async with client.stream(path) as response:
        assert response.status_code == 200


async def test_a_file_url_pointing_off_camera_is_refused(client: OscHttpClient) -> None:
    """The URL arrives in a device response.

    A malformed or hostile payload must not make SphereLoom fetch an arbitrary host, with
    this client's headers attached, on behalf of a camera.
    """
    with pytest.raises(InvalidArgumentError) as caught:
        async with client.stream("http://example.invalid/DCIM/Camera01/IMG_0001.jpg"):
            pass  # pragma: no cover - the context manager raises on entry

    assert caught.value.details["received_host"] == "example.invalid"


@pytest.mark.parametrize(
    "hostile",
    [
        "http://127.0.0.1:1/secret",
        "https://127.0.0.1/secret",
        "http://evil.example/IMG.jpg",
    ],
)
async def test_off_origin_urls_are_refused_whatever_shape_they_take(
    client: OscHttpClient, hostile: str
) -> None:
    with pytest.raises(InvalidArgumentError):
        async with client.stream(hostile):
            pass  # pragma: no cover - the context manager raises on entry


# ---------------------------------------------------------------- timeouts


async def test_a_slow_response_maps_to_the_timeout_taxonomy() -> None:
    """A camera that accepts the connection but never answers is a real failure mode.

    It is distinct from an unreachable camera, and the taxonomy must tell them apart so an
    agent can say something useful.
    """
    slow = FakeCamera(scenario=scenarios.Scenario(latency_seconds=2.0))
    with run_fake_camera(slow) as base_url:
        http = OscHttpClient(base_url, read_timeout=0.2)
        try:
            with pytest.raises(OperationTimeoutError) as caught:
                await http.state()
        finally:
            await http.aclose()

    assert "timeout" in caught.value.message.lower()
    assert caught.value.retryable is True


async def test_a_stale_cache_is_not_reported_as_fresh_after_a_failed_refresh() -> None:
    """The throttle marker and the cache timestamp are different facts.

    Conflating them means every failed refresh makes an old payload look newly fetched,
    which is the opposite of what the age field exists to tell a caller.
    """
    camera = FakeCamera()
    monotonic = FakeMonotonic()
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url, monotonic=monotonic)
        try:
            await http.info()
            monotonic.advance(INFO_MIN_INTERVAL_SECONDS + 0.1)

            # The camera goes away, so the refresh fails and leaves the old payload behind.
            await http._client.aclose()
            with pytest.raises((NotConnectedError, OperationTimeoutError, RuntimeError)):
                await http.info()

            cached = http._cached_info()
        finally:
            with contextlib.suppress(RuntimeError):
                await http.aclose()

    assert cached is None, "a stale payload was reported as inside the freshness window"


async def test_an_ambiguous_failure_after_an_unsafe_command_is_not_advertised_as_retryable() -> (
    None
):
    """Not retrying is only half the fix. The envelope must not invite a retry either.

    An agent reading `retryable: true` will repeat the call, producing exactly the
    duplicate capture the client's own retry policy exists to prevent -- one layer further
    out, where the policy cannot reach.
    """
    slow = FakeCamera(scenario=scenarios.Scenario(latency_seconds=2.0))
    with run_fake_camera(slow) as base_url:
        http = OscHttpClient(base_url, read_timeout=0.2)
        try:
            with pytest.raises(OperationTimeoutError) as caught:
                await http.execute("camera.takePicture")
        finally:
            await http.aclose()

    assert caught.value.retryable is False
    body = caught.value.to_envelope()["error"]
    assert isinstance(body, dict)
    assert body["retryable"] is False


async def test_an_ambiguous_failure_after_a_safe_command_stays_retryable() -> None:
    """Repeating a listing is harmless, so the caller should be told it may."""
    slow = FakeCamera(scenario=scenarios.Scenario(latency_seconds=2.0))
    with run_fake_camera(slow) as base_url:
        http = OscHttpClient(base_url, read_timeout=0.2)
        try:
            with pytest.raises(OperationTimeoutError) as caught:
                await http.execute("camera.listFiles")
        finally:
            await http.aclose()

    assert caught.value.retryable is True


async def test_a_timeout_after_acceptance_is_not_advertised_as_retryable() -> None:
    """The camera accepted the command and may still be running it.

    The command id is the way forward; repeating the command is not.
    """
    stuck = FakeCamera(capture_polls=10_000)
    with run_fake_camera(stuck) as base_url:
        http = OscHttpClient(base_url)
        runner = CommandRunner(http)
        try:
            with pytest.raises(OperationTimeoutError) as caught:
                await runner.run("camera.takePicture", deadline_seconds=0.3)
        finally:
            await http.aclose()

    assert caught.value.retryable is False
    assert caught.value.details["command_id"]


async def test_an_oversized_response_is_refused_rather_than_read() -> None:
    """The camera is unauthenticated and on a network SphereLoom does not control.

    Reading an unbounded body from it would let a hostile or broken responder exhaust
    memory, so the read is capped while streaming rather than after the fact.
    """
    camera = FakeCamera(scenario=scenarios.OVERSIZED)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        try:
            with pytest.raises(InternalError) as caught:
                await http.state()
        finally:
            await http.aclose()

    assert caught.value.details["limit_bytes"] == MAX_RESPONSE_BYTES


async def test_a_normal_response_is_well_under_the_limit(client: OscHttpClient) -> None:
    """The cap must be generous enough that real listings never approach it."""
    payload = await client.execute("camera.listFiles", {"fileType": "all", "entryCount": 50})

    assert payload["state"] == "done"


async def test_a_redirect_in_the_json_path_is_not_accepted_as_a_result() -> None:
    """Redirects are not followed, so a 3xx body is not an OSC response.

    Accepting it would treat a redirect page as a command result. The same rule already
    guarded downloads; it was missing here, which is the same defect in a second place.
    """
    camera = FakeCamera(scenario=scenarios.REDIRECTING)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        try:
            with pytest.raises(InternalError) as caught:
                await http.state()
        finally:
            await http.aclose()

    assert caught.value.details["status_code"] == 302


async def test_a_server_error_after_an_unsafe_command_is_not_retryable() -> None:
    """A 5xx does not prove the camera did nothing before failing."""
    camera = FakeCamera(scenario=scenarios.SERVER_ERROR)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        try:
            with pytest.raises(NotConnectedError) as caught:
                await http.execute("camera.takePicture")
        finally:
            await http.aclose()

    assert caught.value.retryable is False


async def test_malformed_json_after_an_unsafe_command_does_not_recommend_retrying() -> None:
    """Advising a retry after a capture of unknown outcome is advice to duplicate it."""
    camera = FakeCamera(scenario=scenarios.MALFORMED_JSON)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        try:
            with pytest.raises(InternalError) as caught:
                await http.execute("camera.takePicture")
        finally:
            await http.aclose()

    assert caught.value.retryable is False
    assert "retrying usually succeeds" not in caught.value.message


async def test_malformed_json_after_a_safe_command_still_recommends_retrying() -> None:
    camera = FakeCamera(scenario=scenarios.MALFORMED_JSON)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        try:
            with pytest.raises(InternalError) as caught:
                await http.execute("camera.listFiles")
        finally:
            await http.aclose()

    assert caught.value.retryable is True


# ---------------------------------------------------------------- untrusted identifiers


@pytest.mark.parametrize(
    "identifier",
    [["a", "list"], {"an": "object"}, 12345, "", "   ", "x" * 500],
)
async def test_an_unusable_command_id_is_reported_as_malformed(
    client: OscHttpClient, monkeypatch: pytest.MonkeyPatch, identifier: object
) -> None:
    """The id comes from a device response and is copied into every poll and message.

    A list would be stringified into its repr; an unbounded string would be carried
    everywhere unchecked.
    """

    async def acknowledged(*args: object, **kwargs: object) -> dict[str, object]:
        return {"name": "camera.takePicture", "state": "inProgress", "id": identifier}

    monkeypatch.setattr(client, "execute", acknowledged)
    runner = CommandRunner(client)

    with pytest.raises(InternalError):
        await runner.run("camera.takePicture")


async def test_a_normal_command_id_is_accepted(runner: CommandRunner) -> None:
    """Being strict must not reject what real firmware actually sends."""
    result = await runner.run("camera.takePicture")

    assert result.command_id is not None
    assert result.command_id.isdigit()


async def test_a_connection_failure_is_retried_even_for_an_unsafe_command() -> None:
    """A setup failure never reached the camera, so repeating it cannot duplicate anything.

    This is tested with `takePicture` on purpose. An earlier version used `listFiles`,
    which is on the safe allowlist, so it passed without ever exercising the branch it
    claimed to cover: a test that cannot fail is worse than no test.

    The failure is injected at the transport, not at the client, so every protocol
    guarantee the client enforces stays intact.
    """
    attempts = 0

    class RefusingTwice(httpx.AsyncBaseTransport):
        def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
            self._inner = inner

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts <= 2:
                message = "connection refused"
                raise httpx.ConnectError(message, request=request)
            return await self._inner.handle_async_request(request)

    camera = FakeCamera()
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url, transport=RefusingTwice(httpx.AsyncHTTPTransport()))
        try:
            payload = await http.execute("camera.takePicture")
        finally:
            await http.aclose()

    assert attempts == 3
    assert payload["state"] == "inProgress"


async def test_connection_retries_are_bounded_for_an_unsafe_command() -> None:
    """Always retrying a setup failure must not mean retrying forever."""
    attempts = 0

    class AlwaysRefusing(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            message = "connection refused"
            raise httpx.ConnectError(message, request=request)

    http = OscHttpClient("http://127.0.0.1:9", transport=AlwaysRefusing())
    try:
        with pytest.raises(NotConnectedError):
            await http.execute("camera.takePicture")
    finally:
        await http.aclose()

    assert attempts == 3


async def test_a_server_error_is_not_retried_for_an_unsafe_command() -> None:
    """A completed 5xx did reach the camera, so repeating a capture could duplicate it."""
    camera = FakeCamera(scenario=scenarios.FLAKY)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        try:
            with pytest.raises(NotConnectedError):
                await http.execute("camera.takePicture")
        finally:
            await http.aclose()

    assert camera.request_log.count("POST /osc/commands/execute") == 1


async def test_a_non_finite_json_constant_is_refused() -> None:
    """Python's decoder accepts NaN and Infinity, which are not valid JSON.

    A value that defeats every numeric comparison must not reach a domain model.
    """
    camera = FakeCamera(scenario=scenarios.NON_FINITE_JSON)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        try:
            with pytest.raises(InternalError):
                await http.state()
        finally:
            await http.aclose()


async def test_an_overflowing_number_literal_is_refused() -> None:
    """`1e400` is well-formed JSON that every parser accepts, and Python renders it `inf`.

    `parse_constant` only sees the bare NaN and Infinity tokens, so this is a separate
    escape route for a value that defeats every numeric comparison.
    """
    camera = FakeCamera(scenario=scenarios.OVERFLOWING_NUMBER)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        try:
            with pytest.raises(InternalError):
                await http.state()
        finally:
            await http.aclose()


async def test_a_deeply_nested_response_is_refused() -> None:
    """Deep nesting raises RecursionError, not ValueError, and would escape the taxonomy."""
    camera = FakeCamera(scenario=scenarios.DEEPLY_NESTED_JSON)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        try:
            with pytest.raises(InternalError):
                await http.state()
        finally:
            await http.aclose()


async def test_a_non_object_payload_is_not_rendered(client: OscHttpClient) -> None:
    """Rendering an arbitrarily large list to produce a short excerpt recreates the
    allocation hazard that the bounded read exists to avoid."""
    camera = FakeCamera(scenario=scenarios.ARRAY_PAYLOAD)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        try:
            with pytest.raises(InternalError) as caught:
                await http.state()
        finally:
            await http.aclose()

    assert caught.value.details["received_type"] == "list"
    assert "excerpt" not in caught.value.details


async def test_a_polling_failure_after_a_capture_is_not_retryable() -> None:
    """The poll is harmless to repeat; the capture it is polling is not.

    Letting the poll's own verdict through would invite a duplicate of an operation the
    camera has already accepted. The poll must actually fail for this to mean anything: a
    deadline expiring instead produces a timeout that is hardcoded non-retryable, which
    would pass this assertion without the override ever running.
    """
    camera = FakeCamera(
        capture_polls=10_000,
        scenario=scenarios.Scenario(failing_paths=frozenset({"/osc/commands/status"})),
    )
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        runner = CommandRunner(http)
        try:
            with pytest.raises(SphereLoomError) as caught:
                await runner.run("camera.takePicture", deadline_seconds=30)
        finally:
            await http.aclose()

    assert not isinstance(caught.value, OperationTimeoutError)
    assert caught.value.retryable is False


async def test_a_read_timeout_is_not_retried_even_for_a_safe_command() -> None:
    """A read timeout does not mean the camera stopped executing.

    The request was already sent, so retrying would put a second command in flight while
    the first may still be running, breaking the vendor's one-at-a-time rule. The retry
    allowlist governs *which commands* may repeat; it cannot make an ambiguous failure
    unambiguous.
    """
    slow = FakeCamera(scenario=scenarios.Scenario(latency_seconds=2.0))
    with run_fake_camera(slow) as base_url:
        http = OscHttpClient(base_url, read_timeout=0.2)
        try:
            with pytest.raises(OperationTimeoutError) as caught:
                await http.execute("camera.getOptions")
        finally:
            await http.aclose()

    assert slow.request_log.count("POST /osc/commands/execute") == 1
    assert "already sent" in caught.value.message


async def test_a_timed_out_capture_is_not_retried() -> None:
    """The camera may have taken the photograph before the response was lost."""
    slow = FakeCamera(scenario=scenarios.Scenario(latency_seconds=2.0))
    with run_fake_camera(slow) as base_url:
        http = OscHttpClient(base_url, read_timeout=0.2)
        try:
            with pytest.raises(OperationTimeoutError):
                await http.execute("camera.takePicture")
        finally:
            await http.aclose()

    assert slow.request_log.count("POST /osc/commands/execute") == 1


async def test_a_connection_failure_is_retried_because_nothing_was_sent() -> None:
    """A connect failure is unambiguous: the camera never saw the request.

    The failure is injected at the transport. Using a scenario that answers HTTP 503 would
    exercise the completed-response path instead, and this test would stay green even if
    connection retries regressed entirely.
    """
    attempts = 0

    class RefusingTwice(httpx.AsyncBaseTransport):
        def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
            self._inner = inner

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts <= 2:
                message = "connection refused"
                raise httpx.ConnectError(message, request=request)
            return await self._inner.handle_async_request(request)

    camera = FakeCamera()
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url, transport=RefusingTwice(httpx.AsyncHTTPTransport()))
        try:
            payload = await http.execute("camera.getOptions")
        finally:
            await http.aclose()

    assert attempts == 3
    assert payload["state"] == "done"


# ---------------------------------------------------------------- malformed responses


async def test_an_unrecognised_state_is_not_treated_as_success(
    runner: CommandRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accepting any non-error state as success turns a malformed reply into an empty
    successful result, which is the most misleading outcome available."""

    async def unexpected_state(*args: object, **kwargs: object) -> dict[str, object]:
        return {"name": "camera.takePicture", "state": "somethingElse"}

    monkeypatch.setattr(runner._client, "execute", unexpected_state)

    with pytest.raises(InternalError):
        await runner.run("camera.takePicture")


async def test_an_empty_response_is_not_treated_as_success(
    runner: CommandRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def empty(*args: object, **kwargs: object) -> dict[str, object]:
        return {}

    monkeypatch.setattr(runner._client, "execute", empty)

    with pytest.raises(InternalError):
        await runner.run("camera.takePicture")


# ---------------------------------------------------------------- deadline handling


async def test_a_zero_deadline_means_zero_not_the_default(client: OscHttpClient) -> None:
    """`or` would silently turn an explicit 0 into the default, changing what was asked."""
    slow = FakeCamera(capture_polls=10_000)
    with run_fake_camera(slow) as base_url:
        http = OscHttpClient(base_url)
        runner = CommandRunner(http, default_deadline=60.0)
        try:
            with pytest.raises(OperationTimeoutError):
                await runner.run("camera.takePicture", deadline_seconds=0)
        finally:
            await http.aclose()


async def test_a_negative_deadline_is_rejected(client: OscHttpClient) -> None:
    runner = CommandRunner(client)

    with pytest.raises(InvalidArgumentError):
        await runner.run("camera.takePicture", deadline_seconds=-1)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
async def test_a_non_finite_deadline_is_rejected(client: OscHttpClient, value: float) -> None:
    """NaN defeats every elapsed-time comparison; infinity silently removes the deadline."""
    runner = CommandRunner(client)

    with pytest.raises(InvalidArgumentError):
        await runner.run("camera.takePicture", deadline_seconds=value)


async def test_a_deadline_is_not_exceeded_by_a_slow_poll() -> None:
    """Checking the deadline only before sleeping makes it advisory.

    A poll can consume its own HTTP timeout on top of an exhausted budget, so the deadline
    is bounded around the poll.
    """
    stuck = FakeCamera(capture_polls=10_000, scenario=scenarios.Scenario(latency_seconds=0.4))
    with run_fake_camera(stuck) as base_url:
        http = OscHttpClient(base_url)
        runner = CommandRunner(http)
        started = time.monotonic()
        try:
            with pytest.raises(OperationTimeoutError) as caught:
                await runner.run("camera.takePicture", deadline_seconds=0.5)
            elapsed = time.monotonic() - started
        finally:
            await http.aclose()

    assert elapsed < 2.0, f"the deadline was exceeded by {elapsed - 0.5:.1f}s"
    # A fractional budget must survive into the message: ":.0f" would report "0 seconds",
    # which reads as a bug rather than an explanation of what was configured.
    assert "0.5 seconds" in caught.value.message


# ---------------------------------------------------------------- streaming failures


async def test_a_missing_file_is_reported_as_not_found(client: OscHttpClient) -> None:
    """A consumer must not have to catch httpx types to handle a deleted file."""
    with pytest.raises(NotFoundError):
        async with client.stream("/DCIM/Camera01/IMG_does_not_exist.jpg"):
            pass  # pragma: no cover - the context manager raises on entry


async def test_an_interrupted_download_maps_to_the_taxonomy() -> None:
    """A dropped transfer is the normal case on a weak access point.

    The failure surfaces while the caller iterates the body, which is exactly the path that
    previously leaked a raw httpx exception.
    """
    camera = FakeCamera(scenario=scenarios.DROPPED_DOWNLOAD)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        try:
            listing = await http.execute("camera.listFiles", {"fileType": "image", "entryCount": 1})
            path = listing["results"]["entries"][0]["_localFileUrl"]

            async def read_everything() -> None:
                async with http.stream(path) as response:
                    async for _ in response.aiter_bytes():
                        pass

            with pytest.raises(NotConnectedError) as caught:
                await read_everything()
        finally:
            await http.aclose()

    assert "interrupted" in caught.value.message.lower()


async def test_a_download_that_never_starts_maps_to_the_timeout_taxonomy() -> None:
    """The response itself is late, so the timeout fires in `send`, before any body."""
    camera = FakeCamera(scenario=scenarios.Scenario(latency_seconds=2.0))
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url, read_timeout=0.2)
        try:
            with pytest.raises(OperationTimeoutError):
                async with http.stream("/DCIM/Camera01/IMG_20260115_103000_00_001.jpg"):
                    pass  # pragma: no cover - the context manager raises on entry
        finally:
            await http.aclose()


async def test_a_download_that_stalls_mid_body_maps_to_the_timeout_taxonomy() -> None:
    """Headers and one chunk arrive, then the camera goes quiet.

    The caller is already iterating the body, so the timeout is thrown back in at the
    `yield` inside the streaming context manager. That is a different branch from a
    response that is merely slow to start, and without it a raw httpx timeout escapes.
    """
    camera = FakeCamera(scenario=scenarios.STALLED_DOWNLOAD)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url, read_timeout=0.3)

        async def consume() -> None:
            async with http.stream("/DCIM/Camera01/IMG_20260115_103000_00_001.jpg") as response:
                async for _ in response.aiter_bytes():
                    pass

        try:
            with pytest.raises(OperationTimeoutError):
                await consume()
        finally:
            await http.aclose()


async def test_a_redirect_is_not_served_as_file_content() -> None:
    """Redirects are deliberately not followed, so a 3xx body is not file content.

    Letting it through would write a redirect page to disk under a media filename.
    """
    camera = FakeCamera()
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        try:
            with pytest.raises(NotConnectedError) as caught:
                async with http.stream("/DCIM/Camera01/redirect-me.jpg"):
                    pass  # pragma: no cover - the context manager raises on entry
        finally:
            await http.aclose()

    assert caught.value.details["status_code"] == 302


async def test_a_malformed_file_url_is_reported_not_crashed(client: OscHttpClient) -> None:
    """The URL came from a device response, so a malformed one is a camera problem."""
    with pytest.raises(InvalidArgumentError):
        async with client.stream("http://[not-a-valid-host/file.jpg"):
            pass  # pragma: no cover - the context manager raises on entry


@pytest.mark.parametrize("value", [["a"], {"a": 1}, 5, None, True])
async def test_a_non_string_file_url_is_mapped_to_the_taxonomy(value: Any) -> None:
    """`httpx.URL` raises TypeError, not ValueError, for a non-string.

    A malformed device payload could otherwise escape as a raw exception.
    """
    http = OscHttpClient("http://192.168.42.1")
    try:
        with pytest.raises(InvalidArgumentError):
            http._validated_url(value)
    finally:
        await http.aclose()


async def test_a_poll_that_answers_past_the_deadline_is_not_accepted() -> None:
    """A poll that returns `done` just after the budget ran out must not be honoured.

    Bounding the wait is not enough on its own: `asyncio.wait_for` only fails when the poll
    is still outstanding. A poll that answers inside its budget, but after the deadline has
    passed, reaches the recheck instead. Time is injected so the moment is exact rather
    than raced.
    """

    class JumpsPastTheDeadline:
        """Reports no elapsed time until the poll has answered, then jumps past any budget.

        The runner reads the clock four times before the poll returns: once to anchor
        `started`, once at the top of the loop, once to bound the sleep and once to size the
        poll budget. Jumping earlier would trip one of those checks instead of the recheck.
        """

        def __init__(self) -> None:
            self._calls = 0

        def elapsed(self) -> float:
            self._calls += 1
            return 0.0 if self._calls <= 4 else 999.0

    camera = FakeCamera(capture_polls=0)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        runner = CommandRunner(http, monotonic=JumpsPastTheDeadline())
        try:
            with pytest.raises(OperationTimeoutError):
                await runner.run("camera.takePicture", deadline_seconds=30)
        finally:
            await http.aclose()


async def test_a_vendor_error_reported_during_polling_is_mapped(
    runner: CommandRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A command can be accepted and then fail. The failure arrives from the poll, not the
    acknowledgement, and must reach the taxonomy like any other vendor error."""

    async def accepted(*args: object, **kwargs: object) -> dict[str, object]:
        return {"name": "camera.takePicture", "state": "inProgress", "id": "cmd-1"}

    async def failed(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "name": "camera.takePicture",
            "state": "error",
            "error": {"code": "disabledCommand", "message": "The card is write protected."},
        }

    monkeypatch.setattr(runner._client, "execute", accepted)
    monkeypatch.setattr(runner._client, "command_status", failed)

    with pytest.raises(SphereLoomError) as caught:
        await runner.run("camera.takePicture", deadline_seconds=5)

    vendor = caught.value.details["vendor"]
    assert isinstance(vendor, dict)
    error = vendor["error"]
    assert isinstance(error, dict)
    assert error["code"] == "disabledCommand"


async def test_an_unrecognised_state_during_polling_is_not_polled_forever(
    runner: CommandRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Looping on a state the client does not understand is worse than refusing it: the
    command would be polled until the deadline with no prospect of a usable answer."""

    async def accepted(*args: object, **kwargs: object) -> dict[str, object]:
        return {"name": "camera.takePicture", "state": "inProgress", "id": "cmd-1"}

    async def nonsense(*args: object, **kwargs: object) -> dict[str, object]:
        return {"name": "camera.takePicture", "state": "somethingElse"}

    monkeypatch.setattr(runner._client, "execute", accepted)
    monkeypatch.setattr(runner._client, "command_status", nonsense)

    with pytest.raises(InternalError):
        await runner.run("camera.takePicture", deadline_seconds=5)
