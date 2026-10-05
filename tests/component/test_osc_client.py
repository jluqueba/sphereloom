"""The OSC client and command runner, exercised against the fake camera over real HTTP.

These are component tests on purpose. The behaviours that matter here — the command lock,
the info cache, retry selectivity, asynchronous completion — only exist because of how a
real camera behaves, and mocking the transport would test our assumptions instead of the
protocol.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import pytest

from sphereloom.adapters.fake import scenarios
from sphereloom.adapters.fake.camera_server import FakeCamera
from sphereloom.adapters.fake.runner import run_fake_camera
from sphereloom.adapters.osc.client import (
    INFO_MIN_INTERVAL_SECONDS,
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
    OperationTimeoutError,
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


async def test_status_polling_does_not_hold_the_command_lock(
    camera: FakeCamera, client: OscHttpClient
) -> None:
    """Holding the lock while awaiting a capture would stall every other call for its
    duration, which would make a status check impossible exactly when it is most wanted."""
    slow = FakeCamera(capture_polls=4)
    with run_fake_camera(slow) as base_url:
        http = OscHttpClient(base_url)
        runner = CommandRunner(http, default_deadline=10.0)
        try:
            capture = asyncio.create_task(runner.run("camera.takePicture"))
            await asyncio.sleep(0.05)
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


async def test_a_capture_is_never_retried() -> None:
    """Repeating takePicture could take a second photograph.

    The client decides this from the command name, so no call site can opt a capture into
    retries by passing the wrong argument.
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


async def test_a_timed_out_idempotent_request_is_retried_but_bounded() -> None:
    """Retrying a read is right; retrying forever is not."""
    slow = FakeCamera(scenario=scenarios.Scenario(latency_seconds=2.0))
    with run_fake_camera(slow) as base_url:
        http = OscHttpClient(base_url, read_timeout=0.2)
        try:
            with pytest.raises(OperationTimeoutError):
                await http.execute("camera.getOptions")
        finally:
            await http.aclose()

    attempts = slow.request_log.count("POST /osc/commands/execute")
    assert 1 < attempts <= 3


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
