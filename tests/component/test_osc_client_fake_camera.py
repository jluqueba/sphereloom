"""The OSC HTTP client against the fake camera over real HTTP on loopback (#16)."""

from __future__ import annotations

import pytest

from sphereloom.adapters.fake import scenarios
from sphereloom.adapters.fake.camera_server import FakeCamera
from sphereloom.adapters.fake.runner import run_fake_camera
from sphereloom.adapters.osc.client import OscEndpoint, OscHttpClient
from sphereloom.domain.errors import ErrorCode, SphereLoomError


def _client(base_url: str) -> OscHttpClient:
    return OscHttpClient(base_url, connect_timeout=5, read_timeout=15)


async def test_the_camera_accepts_the_client_s_protocol_headers() -> None:
    """The fake rejects any request without the OSC header with a 403."""
    with run_fake_camera(FakeCamera()) as base_url:
        async with _client(base_url) as client:
            info = await client.request(OscEndpoint.INFO)
            state = await client.request(OscEndpoint.STATE)
            options = await client.request(
                OscEndpoint.EXECUTE,
                {"name": "camera.getOptions", "parameters": {"optionNames": ["captureMode"]}},
            )

    assert info["model"]
    assert "state" in state
    assert options["state"] == "done"


@pytest.mark.parametrize(
    ("scenario", "code", "reason"),
    [
        (scenarios.SERVER_ERROR, ErrorCode.NOT_CONNECTED, "server_error"),
        (scenarios.MALFORMED_JSON, ErrorCode.INTERNAL, "malformed"),
        (scenarios.FLAKY, ErrorCode.CAMERA_BUSY, "service_unavailable"),
    ],
    ids=["server-error", "malformed-json", "service-unavailable"],
)
async def test_camera_failures_become_taxonomy_errors(
    scenario: scenarios.Scenario, code: ErrorCode, reason: str
) -> None:
    with run_fake_camera(FakeCamera(scenario=scenario)) as base_url:
        async with _client(base_url) as client:
            with pytest.raises(SphereLoomError) as caught:
                await client.request(OscEndpoint.EXECUTE, {"name": "camera.takePicture"})

    error = caught.value
    assert error.code is code
    assert error.details.get("reason", error.details.get("decode_error")) == reason
    assert error.retryable is False
