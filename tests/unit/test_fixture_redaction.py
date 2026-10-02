"""Redaction of recorded camera fixtures.

These tests matter more than their size suggests. The capture script's output is committed
to a public repository, and the raw responses it starts from contain a device serial number
and base64 thumbnails of the owner's own photographs. A gap here publishes someone's data.

Payloads below are synthetic, modelled on the vendor's documented response shapes. No real
camera output is used.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "capture_osc_fixtures.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("capture_osc_fixtures", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


capture_osc_fixtures = _load_script()
redact = capture_osc_fixtures.redact
PLACEHOLDER = capture_osc_fixtures.PLACEHOLDER


INFO_PAYLOAD: dict[str, Any] = {
    "manufacturer": "Example Vision",
    "model": "Example X5",
    "serialNumber": "IXE3619AYS76P6",
    "firmwareVersion": "v1.2.34",
    "endpoints": {"httpPort": 80, "httpUpdatesPort": 80},
    "gps": False,
    "gyro": True,
    "uptime": 48,
    "api": ["/osc/info", "/osc/state"],
    "apiLevel": [2],
    "_sensorModuleType": "4K",
}

LIST_FILES_PAYLOAD: dict[str, Any] = {
    "name": "camera.listFiles",
    "state": "done",
    "results": {
        "entries": [
            {
                "name": "IMG_20260817_193402_00_017.jpg",
                "fileUrl": "http://192.168.42.1:80/DCIM/Camera01/IMG_20260817_193402_00_017.jpg",
                "_localFileUrl": "/DCIM/Camera01/IMG_20260817_193402_00_017.jpg",
                "size": 4494224,
                "width": 6080,
                "height": 3040,
                "dateTimeZone": "2026:08:17 19:34:02+02:00",
                "isProcessed": True,
                "previewUrl": "http://192.168.42.1/preview/abc",
                "thumbnail": "/9j/2wCEAAIREALBASE64IMAGEDATA==",
            }
        ],
        "totalEntries": 3,
    },
}


def test_the_serial_number_is_removed() -> None:
    """A serial number identifies one physical device belonging to one person."""
    cleaned = redact(INFO_PAYLOAD)

    assert cleaned["serialNumber"] == PLACEHOLDER
    assert "IXE3619AYS76P6" not in str(cleaned)


def test_the_model_and_firmware_survive() -> None:
    """Redaction must not destroy what the fixtures exist to record."""
    cleaned = redact(INFO_PAYLOAD)

    assert cleaned["model"] == "Example X5"
    assert cleaned["firmwareVersion"] == "v1.2.34"


def test_nested_thumbnails_are_removed() -> None:
    """Thumbnails are base64 image data from the owner's photographs, nested two levels in."""
    cleaned = redact(LIST_FILES_PAYLOAD)
    entry = cleaned["results"]["entries"][0]

    assert entry["thumbnail"] == PLACEHOLDER
    assert "REALBASE64IMAGEDATA" not in str(cleaned)


def test_preview_urls_are_removed() -> None:
    cleaned = redact(LIST_FILES_PAYLOAD)

    assert cleaned["results"]["entries"][0]["previewUrl"] == PLACEHOLDER


def test_filename_dates_are_normalised() -> None:
    """A filename discloses when and how often someone was recording."""
    cleaned = redact(LIST_FILES_PAYLOAD)
    entry = cleaned["results"]["entries"][0]

    assert "20260817" not in str(cleaned)
    assert "193402" not in str(cleaned)
    assert entry["name"] == "IMG_20200101_000000_00_017.jpg"
    assert entry["_localFileUrl"] == "/DCIM/Camera01/IMG_20200101_000000_00_017.jpg"


def test_filename_structure_survives_normalisation() -> None:
    """Contract tests assert on shape, so the vendor's naming convention must remain."""
    cleaned = redact(LIST_FILES_PAYLOAD)
    name = cleaned["results"]["entries"][0]["name"]

    assert name.startswith("IMG_")
    assert name.endswith("_017.jpg")


def test_capture_timestamps_are_normalised() -> None:
    cleaned = redact(LIST_FILES_PAYLOAD)

    assert cleaned["results"]["entries"][0]["dateTimeZone"] == capture_osc_fixtures.FIXED_DATETIME


def test_uptime_is_normalised() -> None:
    assert redact(INFO_PAYLOAD)["uptime"] == 0


def test_non_sensitive_values_are_untouched() -> None:
    cleaned = redact(LIST_FILES_PAYLOAD)
    entry = cleaned["results"]["entries"][0]

    assert entry["size"] == 4494224
    assert entry["width"] == 6080
    assert entry["isProcessed"] is True
    assert cleaned["results"]["totalEntries"] == 3


def test_capability_flags_keep_their_type() -> None:
    """`"gps": false` states what the hardware has; it is not personal data.

    Rewriting it would corrupt the schema these fixtures exist to capture.
    """
    cleaned = redact(INFO_PAYLOAD)

    assert cleaned["gps"] is False
    assert cleaned["gyro"] is True


def test_gps_coordinates_are_still_removed() -> None:
    """A capability flag is safe; actual coordinates are not."""
    cleaned = redact({"gpsInfo": {"lat": 40.4168, "lng": -3.7038}})

    assert cleaned["gpsInfo"] == {"lat": PLACEHOLDER, "lng": PLACEHOLDER}


def test_the_manifest_records_only_real_changes() -> None:
    """A key listed as redacted but left untouched would make the audit trail a lie."""
    found: set[str] = set()
    redact({"gps": False, "serialNumber": "ABC123"}, found=found)

    assert "serialNumber" in found
    assert "gps" not in found


def test_the_original_payload_is_not_mutated() -> None:
    """The caller keeps the raw response; redaction must return a copy."""
    redact(INFO_PAYLOAD)

    assert INFO_PAYLOAD["serialNumber"] == "IXE3619AYS76P6"


def test_redaction_reports_what_it_found() -> None:
    """The manifest records which keys were redacted, so the procedure is auditable."""
    found: set[str] = set()
    redact(LIST_FILES_PAYLOAD, found=found)

    assert {"thumbnail", "previewUrl", "name", "fileUrl"} <= found


def test_redaction_is_idempotent() -> None:
    once = redact(LIST_FILES_PAYLOAD)
    twice = redact(once)

    assert once == twice


@pytest.mark.parametrize(
    "key",
    ["ssid", "_ssid", "password", "token", "gpsInfo"],
)
def test_other_sensitive_keys_are_removed(key: str) -> None:
    """Firmware revisions differ, so the key set is defensive rather than minimal."""
    cleaned = redact({key: "sensitive-value", "keep": "visible"})

    assert cleaned[key] == PLACEHOLDER
    assert cleaned["keep"] == "visible"


def test_a_sensitive_list_keeps_its_type() -> None:
    cleaned = redact({"thumbnail": ["a", "b"]})

    assert cleaned["thumbnail"] == [PLACEHOLDER, PLACEHOLDER]


def test_file_url_lists_are_normalised() -> None:
    """Recording results return several files, so the list form must be handled too."""
    payload = {
        "fileUrls": [
            "http://192.168.42.1:80/DCIM/Camera01/VID_20260817_172302_10_005.mp4",
            "http://192.168.42.1:80/DCIM/Camera01/VID_20260817_172302_00_005.mp4",
        ]
    }

    cleaned = redact(payload)

    assert all("20260817" not in url for url in cleaned["fileUrls"])
    assert all(url.endswith(".mp4") for url in cleaned["fileUrls"])
    assert len(cleaned["fileUrls"]) == 2


def test_a_stale_optional_fixture_is_removed(tmp_path: Path) -> None:
    """A fixture from an earlier session must not be attributed to this one.

    A read-only run after an earlier `--capture-photo` run would otherwise leave the old
    capture response in place while writing a fresh manifest, quietly claiming that response
    came from the current firmware on the current date.
    """
    output = tmp_path / "osc"
    output.mkdir()
    stale = output / "take_picture_result.json"
    stale.write_text('{"from": "an earlier session"}', encoding="utf-8")

    captured = {"info": {"model": "Example X5", "firmwareVersion": "v1.2.34"}}
    _write_fixtures(captured, output)

    assert not stale.exists()


def test_unrelated_files_are_left_alone(tmp_path: Path) -> None:
    """Only fixtures this script generates are removed; hand-added files are not ours."""
    output = tmp_path / "osc"
    output.mkdir()
    handmade = output / "notes-from-the-maintainer.json"
    handmade.write_text("{}", encoding="utf-8")

    _write_fixtures({"info": {"model": "Example X5"}}, output)

    assert handmade.exists()


def test_the_manifest_lists_the_fixtures_actually_written(tmp_path: Path) -> None:
    output = tmp_path / "osc"
    output.mkdir()

    _write_fixtures({"info": {"model": "Example X5"}, "state": {}}, output)

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["fixtures"] == ["info", "state"]


def _write_fixtures(captured: dict[str, Any], output: Path) -> None:
    """Drive the script's output stage directly, without needing a camera."""
    capture_osc_fixtures.write_outputs(captured, output)
