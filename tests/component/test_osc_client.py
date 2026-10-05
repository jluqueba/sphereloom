"""The OSC client and command runner, exercised against the fake camera over real HTTP.

These are component tests on purpose. The behaviours that matter here — the command lock,
the info cache, retry selectivity, asynchronous completion — only exist because of how a
real camera behaves, and mocking the transport would test our assumptions instead of the
protocol.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from sphereloom.adapters.fake import scenarios
from sphereloom.adapters.fake.camera_server import FakeCamera
from sphereloom.adapters.fake.runner import run_fake_camera
from sphereloom.adapters.osc.client import INFO_MIN_INTERVAL_SECONDS, OscHttpClient
from sphereloom.adapters.osc.commands import CommandRunner
from sphereloom.domain.clock import FakeClock
from sphereloom.domain.errors import (
    CameraBusyError,
    InternalError,
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
    clock = FakeClock()
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url, clock=clock)
        try:
            await http.info()
            clock.advance(INFO_MIN_INTERVAL_SECONDS + 0.1)
            await http.info()
        finally:
            await http.aclose()

    assert camera.request_log.count("GET /osc/info") == 2


async def test_a_forced_refresh_bypasses_the_cache(
    camera: FakeCamera, client: OscHttpClient
) -> None:
    await client.info()
    await client.info(force_refresh=True)

    assert camera.request_log.count("GET /osc/info") == 2


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
            payload = await http.execute("camera.getOptions", retryable=True)
        finally:
            await http.aclose()

    assert payload["state"] == "done"


async def test_a_capture_is_never_retried() -> None:
    """Repeating takePicture could take a second photograph.

    A duplicated shot is worse than a clear error, so capture commands opt out of retries.
    """
    camera = FakeCamera(scenario=scenarios.FLAKY)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        try:
            with pytest.raises(NotConnectedError):
                await http.execute("camera.takePicture")
        finally:
            await http.aclose()

    executions = [entry for entry in camera.request_log if "takePicture" in entry]
    assert len(executions) == 1


async def test_retries_are_bounded() -> None:
    """A camera that is simply down must fail promptly, not retry forever."""
    camera = FakeCamera(scenario=scenarios.SERVER_ERROR)
    with run_fake_camera(camera) as base_url:
        http = OscHttpClient(base_url)
        try:
            with pytest.raises(NotConnectedError):
                await http.execute("camera.getOptions", retryable=True)
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
    listing = await client.execute(
        "camera.listFiles", {"fileType": "image", "entryCount": 1}, retryable=True
    )
    entry = listing["results"]["entries"][0]

    chunks = 0
    received = 0
    async with client.stream(entry["fileUrl"]) as response:
        async for chunk in response.aiter_bytes():
            chunks += 1
            received += len(chunk)

    assert received == entry["size"]
