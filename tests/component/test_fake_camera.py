"""The fake camera must behave like the protocol says, or the suite proves nothing.

These tests exercise the fake directly over real HTTP. They are deliberately written against
the vendor's documented response shapes rather than against the implementation, so a change
that drifts from the protocol fails here rather than silently teaching the adapter a habit
real hardware does not have.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from sphereloom.adapters.fake import scenarios
from sphereloom.adapters.fake.camera_server import FakeCamera
from sphereloom.adapters.fake.runner import run_fake_camera

HEADERS = {"X-XSRF-Protected": "1"}


@pytest.fixture
def camera() -> FakeCamera:
    return FakeCamera()


@pytest.fixture
def client(camera: FakeCamera) -> Any:
    with (
        run_fake_camera(camera) as base_url,
        httpx.Client(base_url=base_url, headers=HEADERS, timeout=10.0) as http,
    ):
        yield http


def _execute(http: httpx.Client, name: str, **parameters: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name}
    if parameters:
        payload["parameters"] = parameters
    response = http.post("/osc/commands/execute", json=payload)
    return dict(response.json())


def _poll_until_done(http: httpx.Client, command_id: str, *, limit: int = 10) -> dict[str, Any]:
    for _ in range(limit):
        body = dict(http.post("/osc/commands/status", json={"id": command_id}).json())
        if body.get("state") == "done":
            return body
    message = f"Command {command_id} never reached the done state."
    raise AssertionError(message)


# ---------------------------------------------------------------- discovery


def test_info_reports_the_documented_endpoint_set(client: httpx.Client) -> None:
    body = client.get("/osc/info").json()

    assert body["api"] == [
        "/osc/info",
        "/osc/state",
        "/osc/checkForUpdates",
        "/osc/commands/execute",
        "/osc/commands/status",
    ]
    assert body["model"]
    assert body["apiLevel"] == [2]


def test_state_reports_battery_as_a_fraction(client: httpx.Client) -> None:
    """The protocol defines battery in the range zero to one, not as a percentage.

    Getting this wrong would make a half-charged camera look empty.
    """
    body = client.post("/osc/state").json()

    assert 0.0 <= body["state"]["batteryLevel"] <= 1.0


def test_state_reports_card_state(client: httpx.Client) -> None:
    body = client.post("/osc/state").json()

    assert body["state"]["_cardState"] == "pass"


# ---------------------------------------------------------------- options


def test_get_options_returns_only_what_was_asked_for(client: httpx.Client) -> None:
    body = _execute(client, "camera.getOptions", optionNames=["captureMode", "totalSpace"])

    assert set(body["results"]["options"]) == {"captureMode", "totalSpace"}


def test_get_options_exposes_vendor_extensions_with_their_prefix(client: httpx.Client) -> None:
    body = _execute(client, "camera.getOptions", optionNames=["_videoType", "_batteryCapacity"])

    assert "_videoType" in body["results"]["options"]
    assert "_batteryCapacity" in body["results"]["options"]


def test_set_options_changes_capture_mode(client: httpx.Client) -> None:
    assert (
        _execute(client, "camera.setOptions", options={"captureMode": "video"})["state"] == "done"
    )

    body = _execute(client, "camera.getOptions", optionNames=["captureMode"])
    assert body["results"]["options"]["captureMode"] == "video"


def test_set_options_rejects_exposure_control(client: httpx.Client) -> None:
    """The vendor documents no exposure control over this protocol.

    The fake refuses it so that an adapter claiming otherwise fails a test rather than
    disappointing someone holding a camera.
    """
    body = _execute(client, "camera.setOptions", options={"iso": 400})

    assert body["state"] == "error"
    assert body["error"]["code"] == "invalidParameterName"


def test_set_options_rejects_an_unknown_capture_mode(client: httpx.Client) -> None:
    body = _execute(client, "camera.setOptions", options={"captureMode": "hologram"})

    assert body["state"] == "error"
    assert body["error"]["code"] == "invalidParameterValue"


# ---------------------------------------------------------------- photo capture


def test_take_picture_completes_asynchronously(client: httpx.Client) -> None:
    """Captures are not synchronous. The command returns an id that must be polled."""
    started = _execute(client, "camera.takePicture")

    assert started["state"] == "inProgress"
    assert started["id"]

    finished = _poll_until_done(client, started["id"])
    assert finished["results"]["fileUrl"].endswith(".jpg")


def test_take_picture_reports_progress_while_running() -> None:
    camera = FakeCamera(capture_polls=3)
    with (
        run_fake_camera(camera) as base_url,
        httpx.Client(base_url=base_url, headers=HEADERS) as http,
    ):
        started = _execute(http, "camera.takePicture")
        body = http.post("/osc/commands/status", json={"id": started["id"]}).json()

        assert body["state"] == "inProgress"
        assert 0.0 <= body["progress"]["completion"] <= 1.0


def test_take_picture_returns_the_vendor_file_group_fields(client: httpx.Client) -> None:
    started = _execute(client, "camera.takePicture")
    finished = _poll_until_done(client, started["id"])

    assert "_fileGroup" in finished["results"]
    assert "_localFileGroup" in finished["results"]


def test_take_picture_fails_in_video_mode(client: httpx.Client) -> None:
    """A real camera refuses this, and the message is the one users will search for."""
    _execute(client, "camera.setOptions", options={"captureMode": "video"})

    body = _execute(client, "camera.takePicture")

    assert body["state"] == "error"
    assert body["error"]["code"] == "disabledCommand"


def test_a_captured_photo_appears_in_the_gallery(client: httpx.Client) -> None:
    before = _execute(client, "camera.listFiles", fileType="image", entryCount=50)
    started = _execute(client, "camera.takePicture")
    _poll_until_done(client, started["id"])

    after = _execute(client, "camera.listFiles", fileType="image", entryCount=50)

    assert after["results"]["totalEntries"] == before["results"]["totalEntries"] + 1


def test_polling_an_unknown_command_id_is_an_error(client: httpx.Client) -> None:
    response = client.post("/osc/commands/status", json={"id": "nope"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalidParameterValue"


# ---------------------------------------------------------------- recording


def test_recording_produces_one_file_per_lens(client: httpx.Client) -> None:
    """A single recording yields several files.

    Anything that assumes one file per recording breaks on real hardware, so the fake
    insists on the awkward truth.
    """
    _execute(client, "camera.setOptions", options={"captureMode": "video"})
    _execute(client, "camera.startCapture")

    body = _execute(client, "camera.stopCapture")

    assert len(body["results"]["fileUrls"]) == 2
    assert len(body["results"]["_localFileUrls"]) == 2


def test_starting_a_recording_twice_is_rejected(client: httpx.Client) -> None:
    _execute(client, "camera.setOptions", options={"captureMode": "video"})
    _execute(client, "camera.startCapture")

    body = _execute(client, "camera.startCapture")

    assert body["state"] == "error"


def test_stopping_without_starting_is_rejected(client: httpx.Client) -> None:
    body = _execute(client, "camera.stopCapture")

    assert body["state"] == "error"
    assert body["error"]["code"] == "disabledCommand"


def test_recording_requires_video_mode(client: httpx.Client) -> None:
    body = _execute(client, "camera.startCapture")

    assert body["state"] == "error"


def test_state_reflects_an_active_recording(client: httpx.Client) -> None:
    _execute(client, "camera.setOptions", options={"captureMode": "video"})
    _execute(client, "camera.startCapture")

    body = client.post("/osc/state").json()

    assert body["state"]["_captureStatus"] == "shooting"


# ---------------------------------------------------------------- gallery


def test_list_files_orders_newest_first(client: httpx.Client) -> None:
    body = _execute(client, "camera.listFiles", fileType="all", entryCount=50)
    timestamps = [entry["dateTimeZone"] for entry in body["results"]["entries"]]

    assert timestamps == sorted(timestamps, reverse=True)


def test_list_files_filters_by_type(client: httpx.Client) -> None:
    images = _execute(client, "camera.listFiles", fileType="image", entryCount=50)
    videos = _execute(client, "camera.listFiles", fileType="video", entryCount=50)

    assert all(e["name"].endswith(".jpg") for e in images["results"]["entries"])
    assert all(e["name"].endswith(".mp4") for e in videos["results"]["entries"])


def test_list_files_honours_entry_count(client: httpx.Client) -> None:
    body = _execute(client, "camera.listFiles", fileType="all", entryCount=1)

    assert len(body["results"]["entries"]) == 1
    assert body["results"]["totalEntries"] > 1


def test_list_files_supports_a_start_position(client: httpx.Client) -> None:
    first = _execute(client, "camera.listFiles", fileType="all", entryCount=1, startPosition=0)
    second = _execute(client, "camera.listFiles", fileType="all", entryCount=1, startPosition=1)

    assert first["results"]["entries"][0]["name"] != second["results"]["entries"][0]["name"]


def test_gallery_entries_carry_the_documented_fields(client: httpx.Client) -> None:
    body = _execute(client, "camera.listFiles", fileType="image", entryCount=1)
    entry = body["results"]["entries"][0]

    for required in ("name", "fileUrl", "_localFileUrl", "size", "dateTimeZone"):
        assert required in entry, f"missing {required}"


# ---------------------------------------------------------------- downloads


def test_a_file_can_be_downloaded_byte_for_byte(client: httpx.Client, camera: FakeCamera) -> None:
    body = _execute(client, "camera.listFiles", fileType="image", entryCount=1)
    entry = body["results"]["entries"][0]

    response = client.get(entry["_localFileUrl"])

    expected = next(f for f in camera.state.files if f.name == entry["name"])
    assert response.content == expected.content


def test_a_downloaded_photo_is_a_real_jpeg(client: httpx.Client) -> None:
    """Downloads must produce a file that opens, not merely one of the right length."""
    body = _execute(client, "camera.listFiles", fileType="image", entryCount=1)

    response = client.get(body["results"]["entries"][0]["_localFileUrl"])

    assert response.content.startswith(b"\xff\xd8\xff")


def test_downloading_an_unknown_file_is_a_404(client: httpx.Client) -> None:
    assert client.get("/DCIM/Camera01/IMG_does_not_exist.jpg").status_code == 404


# ---------------------------------------------------------------- deletion


def test_deleting_a_file_removes_it(client: httpx.Client) -> None:
    listing = _execute(client, "camera.listFiles", fileType="image", entryCount=1)
    target = listing["results"]["entries"][0]

    body = _execute(client, "camera.delete", fileUrls=[target["fileUrl"]])

    assert body["state"] == "done"
    remaining = _execute(client, "camera.listFiles", fileType="image", entryCount=50)
    assert all(e["name"] != target["name"] for e in remaining["results"]["entries"])


def test_deleting_an_unknown_file_reports_which_one(client: httpx.Client) -> None:
    body = _execute(client, "camera.delete", fileUrls=["http://x/DCIM/Camera01/ghost.jpg"])

    assert body["error"]["code"] == "invalidParameterValue"
    assert "ghost.jpg" in body["error"]["message"]


def test_a_failed_delete_removes_nothing(client: httpx.Client) -> None:
    """Partial deletion would be worse than refusing outright."""
    before = _execute(client, "camera.listFiles", fileType="all", entryCount=50)
    real = before["results"]["entries"][0]["fileUrl"]

    _execute(client, "camera.delete", fileUrls=[real, "http://x/DCIM/Camera01/ghost.jpg"])

    after = _execute(client, "camera.listFiles", fileType="all", entryCount=50)
    assert after["results"]["totalEntries"] == before["results"]["totalEntries"]


# ---------------------------------------------------------------- failure injection


def test_a_busy_camera_reports_a_vendor_error() -> None:
    camera = FakeCamera(scenario=scenarios.BUSY)
    with (
        run_fake_camera(camera) as base_url,
        httpx.Client(base_url=base_url, headers=HEADERS) as http,
    ):
        body = _execute(http, "camera.takePicture")

    assert body["state"] == "error"
    assert body["error"]["code"] == "disabledCommand"


def test_an_unactivated_camera_says_so() -> None:
    """Nothing SphereLoom does can fix this, so the message must reach the user intact."""
    camera = FakeCamera(scenario=scenarios.UNACTIVATED)
    with (
        run_fake_camera(camera) as base_url,
        httpx.Client(base_url=base_url, headers=HEADERS) as http,
    ):
        body = _execute(http, "camera.takePicture")

    assert body["error"]["code"] == "unactivated"


def test_a_full_card_refuses_capture() -> None:
    camera = FakeCamera(scenario=scenarios.STORAGE_FULL)
    with (
        run_fake_camera(camera) as base_url,
        httpx.Client(base_url=base_url, headers=HEADERS) as http,
    ):
        body = _execute(http, "camera.takePicture")
        state = http.post("/osc/state").json()

    assert body["error"]["code"] == "noFreeSpace"
    assert state["state"]["_cardState"] == "noSpace"


def test_malformed_json_is_actually_malformed() -> None:
    """Some firmware returns broken JSON under load. The adapter must survive it."""
    camera = FakeCamera(scenario=scenarios.MALFORMED_JSON)
    with (
        run_fake_camera(camera) as base_url,
        httpx.Client(base_url=base_url, headers=HEADERS) as http,
    ):
        response = http.post("/osc/state")

    with pytest.raises(ValueError, match=r"(?i)json|expecting"):
        response.json()


def test_a_server_error_is_surfaced_as_a_5xx() -> None:
    camera = FakeCamera(scenario=scenarios.SERVER_ERROR)
    with (
        run_fake_camera(camera) as base_url,
        httpx.Client(base_url=base_url, headers=HEADERS) as http,
    ):
        response = http.post("/osc/commands/execute", json={"name": "camera.takePicture"})

    assert response.status_code == 500


def test_a_flaky_camera_recovers_after_failing() -> None:
    """Proves recovery, not merely that failure is noticed."""
    camera = FakeCamera(scenario=scenarios.FLAKY)
    with (
        run_fake_camera(camera) as base_url,
        httpx.Client(base_url=base_url, headers=HEADERS) as http,
    ):
        first = http.post("/osc/commands/execute", json={"name": "camera.getOptions"})
        second = http.post("/osc/commands/execute", json={"name": "camera.getOptions"})
        third = http.post("/osc/commands/execute", json={"name": "camera.getOptions"})

    assert first.status_code == 503
    assert second.status_code == 503
    assert third.status_code == 200


def test_a_truncated_download_is_detectable() -> None:
    """A partial transfer must be detectable, because the file will look plausible.

    HTTP cannot deliver fewer bytes than Content-Length promises without the connection
    breaking, so truncation always arrives as a transport error plus a short file. The
    download worker therefore has to verify the byte count rather than trusting that a
    completed read means a complete file.
    """
    camera = FakeCamera(scenario=scenarios.TRUNCATED_DOWNLOAD)
    received = 0
    declared = 0

    with (
        run_fake_camera(camera) as base_url,
        httpx.Client(base_url=base_url, headers=HEADERS) as http,
    ):
        listing = _execute(http, "camera.listFiles", fileType="image", entryCount=1)
        path = listing["results"]["entries"][0]["_localFileUrl"]

        def read_everything() -> None:
            nonlocal received, declared
            with http.stream("GET", path) as response:
                declared = int(response.headers["Content-Length"])
                for chunk in response.iter_bytes():
                    received += len(chunk)

        with pytest.raises(httpx.HTTPError):
            read_everything()

    assert declared > 0
    assert received < declared


def test_a_dropped_download_fails_rather_than_returning_a_short_file() -> None:
    camera = FakeCamera(scenario=scenarios.DROPPED_DOWNLOAD)

    with (
        run_fake_camera(camera) as base_url,
        httpx.Client(base_url=base_url, headers=HEADERS) as http,
    ):
        listing = _execute(http, "camera.listFiles", fileType="image", entryCount=1)
        path = listing["results"]["entries"][0]["_localFileUrl"]

        with pytest.raises(httpx.HTTPError):
            http.get(path)


def test_an_unreachable_endpoint_fails_the_call() -> None:
    camera = FakeCamera(scenario=scenarios.Scenario(failing_paths=frozenset({"/osc/info"})))
    with (
        run_fake_camera(camera) as base_url,
        httpx.Client(base_url=base_url, headers=HEADERS) as http,
    ):
        response = http.get("/osc/info")

    assert response.status_code == 503


# ---------------------------------------------------------------- protocol discipline


def test_the_camera_records_the_requests_it_received(
    client: httpx.Client, camera: FakeCamera
) -> None:
    """The request log lets tests assert on call discipline, such as polling restraint."""
    client.get("/osc/info")
    client.post("/osc/state")

    assert "GET /osc/info" in camera.request_log
    assert "POST /osc/state" in camera.request_log


def test_sequential_commands_are_not_flagged_as_concurrent(
    client: httpx.Client, camera: FakeCamera
) -> None:
    _execute(client, "camera.getOptions")
    _execute(client, "camera.getOptions")

    assert camera.concurrent_command_detected is False


# ---------------------------------------------------------------- required header


def test_protocol_requests_without_the_required_header_are_rejected() -> None:
    """The vendor requires a static header on every protocol request.

    The fake enforces it so that an adapter which forgets it fails here, rather than
    passing every test and then failing against someone's camera.
    """
    camera = FakeCamera()
    with run_fake_camera(camera) as base_url, httpx.Client(base_url=base_url) as bare:
        assert bare.get("/osc/info").status_code == 403
        assert bare.post("/osc/state").status_code == 403
        assert (
            bare.post("/osc/commands/execute", json={"name": "camera.getOptions"}).status_code
            == 403
        )
        assert bare.post("/osc/commands/status", json={"id": "1"}).status_code == 403


def test_a_wrong_header_value_is_rejected() -> None:
    camera = FakeCamera()
    with (
        run_fake_camera(camera) as base_url,
        httpx.Client(base_url=base_url, headers={"X-XSRF-Protected": "0"}) as wrong,
    ):
        assert wrong.get("/osc/info").status_code == 403


def test_downloads_do_not_require_the_protocol_header() -> None:
    """File downloads are plain HTTP requests to a file URL.

    The vendor documents no header requirement for them, and inventing one would be its own
    kind of infidelity.
    """
    camera = FakeCamera()
    with (
        run_fake_camera(camera) as base_url,
        httpx.Client(base_url=base_url, headers=HEADERS) as http,
    ):
        listing = _execute(http, "camera.listFiles", fileType="image", entryCount=1)
        path = listing["results"]["entries"][0]["_localFileUrl"]

        with httpx.Client(base_url=base_url) as bare:
            assert bare.get(path).status_code == 200


# ---------------------------------------------------------------- option fidelity


def test_setting_white_balance_actually_changes_it(client: httpx.Client) -> None:
    """A setter that reports success without changing anything is worse than one that fails.

    The adapter would learn a habit that silently does nothing on real hardware.
    """
    assert (
        _execute(client, "camera.setOptions", options={"whiteBalance": "daylight"})["state"]
        == "done"
    )

    body = _execute(client, "camera.getOptions", optionNames=["whiteBalance"])
    assert body["results"]["options"]["whiteBalance"] == "daylight"


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("photoStitching", "hologram"),
        ("_videoType", "interpretive-dance"),
        ("whiteBalance", "ultraviolet"),
        ("exposureDelay", 7),
    ],
)
def test_values_outside_the_advertised_support_list_are_rejected(
    client: httpx.Client, option: str, value: object
) -> None:
    body = _execute(client, "camera.setOptions", options={option: value})

    assert body["state"] == "error"
    assert body["error"]["code"] == "invalidParameterValue"


def test_unknown_options_are_rejected(client: httpx.Client) -> None:
    body = _execute(client, "camera.setOptions", options={"warpDrive": "engage"})

    assert body["state"] == "error"
    assert body["error"]["code"] == "invalidParameterName"


@pytest.mark.parametrize(
    ("option", "value"),
    [
        # In Python `False == 0`, so a naive membership test would accept this against the
        # accepted value 0, store the boolean, and hand back the wrong JSON type.
        ("exposureDelay", False),
        ("exposureDelay", True),
        # And `1 == True`, so the mirror case has to be rejected too.
        ("_MuteEnable", 1),
        ("_MuteEnable", 0),
    ],
)
def test_a_value_of_the_wrong_json_type_is_rejected(
    client: httpx.Client, option: str, value: object
) -> None:
    """Type confusion must not pass, or the fake blesses a malformed adapter request."""
    body = _execute(client, "camera.setOptions", options={option: value})

    assert body["state"] == "error"
    assert body["error"]["code"] == "invalidParameterValue"


def test_a_rejected_type_leaves_the_option_untouched(client: httpx.Client) -> None:
    before = _execute(client, "camera.getOptions", optionNames=["_MuteEnable"])

    _execute(client, "camera.setOptions", options={"_MuteEnable": 1})

    after = _execute(client, "camera.getOptions", optionNames=["_MuteEnable"])
    assert after["results"]["options"]["_MuteEnable"] is False
    assert after["results"]["options"] == before["results"]["options"]


def test_correctly_typed_values_are_still_accepted(client: httpx.Client) -> None:
    """The type check must not become so strict that valid requests start failing."""
    assert _execute(client, "camera.setOptions", options={"_MuteEnable": True})["state"] == "done"
    assert _execute(client, "camera.setOptions", options={"exposureDelay": 5})["state"] == "done"

    body = _execute(client, "camera.getOptions", optionNames=["_MuteEnable", "exposureDelay"])
    assert body["results"]["options"]["_MuteEnable"] is True
    assert body["results"]["options"]["exposureDelay"] == 5


def test_read_only_options_are_rejected(client: httpx.Client) -> None:
    body = _execute(client, "camera.setOptions", options={"totalSpace": 1})

    assert body["state"] == "error"
    assert body["error"]["code"] == "invalidParameterName"


def test_a_rejected_batch_changes_nothing(client: httpx.Client) -> None:
    """Validation happens before assignment, so a bad value cannot half-apply a change."""
    before = _execute(client, "camera.getOptions", optionNames=["captureMode"])

    _execute(
        client,
        "camera.setOptions",
        options={"captureMode": "video", "whiteBalance": "ultraviolet"},
    )

    after = _execute(client, "camera.getOptions", optionNames=["captureMode"])
    assert after["results"]["options"] == before["results"]["options"]


def test_advertised_support_lists_match_what_is_accepted(client: httpx.Client) -> None:
    """`getOptions` and `setOptions` are driven by one table, and this proves it."""
    advertised = _execute(client, "camera.getOptions", optionNames=["whiteBalanceSupport"])
    for value in advertised["results"]["options"]["whiteBalanceSupport"]:
        body = _execute(client, "camera.setOptions", options={"whiteBalance": value})
        assert body["state"] == "done", f"{value} is advertised but rejected"


# ---------------------------------------------------------------- failure selectivity


@pytest.mark.parametrize(
    "path",
    ["/osc/info", "/osc/state", "/osc/commands/execute", "/osc/commands/status"],
)
def test_a_failing_path_scenario_targets_the_endpoint_it_names(path: str) -> None:
    """The selector has to work for every endpoint, or a test silently exercises success."""
    camera = FakeCamera(scenario=scenarios.Scenario(failing_paths=frozenset({path})))
    with (
        run_fake_camera(camera) as base_url,
        httpx.Client(base_url=base_url, headers=HEADERS) as http,
    ):
        if path == "/osc/info":
            response = http.get(path)
        elif path == "/osc/commands/execute":
            response = http.post(path, json={"name": "camera.getOptions"})
        elif path == "/osc/commands/status":
            response = http.post(path, json={"id": "000001"})
        else:
            response = http.post(path)

    assert response.status_code == 503


def test_an_untargeted_endpoint_keeps_working() -> None:
    camera = FakeCamera(scenario=scenarios.Scenario(failing_paths=frozenset({"/osc/state"})))
    with (
        run_fake_camera(camera) as base_url,
        httpx.Client(base_url=base_url, headers=HEADERS) as http,
    ):
        assert http.post("/osc/state").status_code == 503
        assert http.get("/osc/info").status_code == 200


# ---------------------------------------------------------------- deletion edge cases


def test_deleting_the_same_file_twice_in_one_call_succeeds(client: httpx.Client) -> None:
    """A repeated reference must not produce a failure that already changed state."""
    listing = _execute(client, "camera.listFiles", fileType="image", entryCount=1)
    url = listing["results"]["entries"][0]["fileUrl"]

    body = _execute(client, "camera.delete", fileUrls=[url, url])

    assert body["state"] == "done"
