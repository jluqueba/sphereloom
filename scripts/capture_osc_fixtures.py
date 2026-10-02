"""Record redacted protocol fixtures from a real camera.

Why this exists
---------------

SphereLoom's test suite runs against a fake camera. That fake is only worth something if it
answers the way a real camera answers, and the vendor's published examples come from an
older model on firmware from several years ago. Recording real responses lets a contract
test assert that the fake and the hardware agree on shape.

Safety
------

This script is **read-only by default**. It never deletes, never formats and never changes a
setting. Capturing a photo is possible but requires an explicit flag.

Everything written to disk passes through `redact()` first, because raw responses contain
material that must never reach a public repository: the camera's serial number, base64
thumbnails of the owner's actual photographs, and filenames that disclose when and how often
someone was recording.

Usage
-----

Join the camera's Wi-Fi access point, then::

    python scripts/capture_osc_fixtures.py
    python scripts/capture_osc_fixtures.py --capture-photo   # also takes one picture
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://192.168.42.1"
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "osc"

#: How long to wait for a capture to report completion before giving up on recording its
#: terminal response. Generous, because a high-resolution still can take a while to write.
CAPTURE_DEADLINE_SECONDS = 60.0

#: The vendor requires this static header on every request. There is no other authentication.
HEADERS = {
    "Content-Type": "application/json;charset=utf-8",
    "Accept": "application/json",
    "X-XSRF-Protected": "1",
}

PLACEHOLDER = "REDACTED"

#: Keys whose value is replaced outright. Thumbnails are base64 image data from the owner's
#: own photographs; serial numbers identify a specific physical device.
REDACT_KEYS = frozenset(
    {
        "serialnumber",
        "thumbnail",
        "_thumbnail",
        "thumbnailurl",
        "previewurl",
        "gps",
        "gpsinfo",
        "_gpsinfo",
        "ssid",
        "_ssid",
        "password",
        "_password",
        "token",
    }
)

#: Keys holding a URL or filename, normalised rather than removed so the schema survives.
PATH_KEYS = frozenset(
    {
        "fileurl",
        "fileurls",
        "_localfileurl",
        "_localfileurls",
        "_filegroup",
        "_localfilegroup",
        "storageuri",
        "name",
        "uri",
    }
)

#: Timestamp-ish keys normalised to a fixed value so fixtures are stable across runs.
TIMESTAMP_KEYS = frozenset({"_systimestamp", "uptime", "_datetime", "datetimezone"})

FIXED_DATE = "20200101"
FIXED_TIME = "000000"
FIXED_DATETIME = "2020:01:01 00:00:00+00:00"

#: Matches the vendor's filename convention, for example IMG_20180106_180200_00_006.jpg.
FILENAME_DATE = re.compile(r"(\d{8})_(\d{6})")


def redact(value: Any, *, found: set[str] | None = None) -> Any:
    """Return a copy of a JSON structure with sensitive material removed.

    Recursion is deliberate: these payloads nest, and a top-level-keys-only approach would
    miss a thumbnail buried inside `results.entries[]`.

    `found` collects the keys whose value actually changed, so the manifest records what was
    really redacted rather than what was merely inspected.
    """
    seen = found if found is not None else set()

    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if lowered in REDACT_KEYS:
                replacement = PLACEHOLDER if isinstance(item, str) else _redact_structure(item)
            elif lowered in TIMESTAMP_KEYS:
                replacement = _normalise_timestamp(item)
            elif lowered in PATH_KEYS:
                replacement = _normalise_paths(item)
            else:
                cleaned[key] = redact(item, found=seen)
                continue

            if replacement != item:
                seen.add(key)
            cleaned[key] = replacement
        return cleaned

    if isinstance(value, list):
        return [redact(item, found=seen) for item in value]

    return value


def _redact_structure(value: Any) -> Any:
    """Replace a non-string sensitive value while preserving its JSON type.

    Booleans are returned untouched. A flag such as `"gps": false` is a capability
    statement, not personal data, and rewriting it would corrupt the very schema these
    fixtures exist to capture.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        return [PLACEHOLDER for _ in value]
    if isinstance(value, dict):
        return dict.fromkeys(value, PLACEHOLDER)
    if isinstance(value, int | float):
        return 0
    return PLACEHOLDER


def _normalise_timestamp(value: Any) -> Any:
    if isinstance(value, str):
        return FIXED_DATETIME
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return 0
    return value


def _normalise_paths(value: Any) -> Any:
    """Normalise dates inside filenames, keeping the vendor's naming structure intact.

    A filename such as IMG_20260817_193402_00_017.jpg tells a reader when the owner was out
    with the camera. The shape matters for contract tests; the date does not.
    """
    if isinstance(value, str):
        return FILENAME_DATE.sub(f"{FIXED_DATE}_{FIXED_TIME}", value)
    if isinstance(value, list):
        return [_normalise_paths(item) for item in value]
    return value


#: Option names requested from the camera. Taken from the vendor's documented example, plus
#: the stitching options that determine whether photos come back usable.
OPTION_NAMES = [
    "iso",
    "isoSupport",
    "shutterSpeed",
    "shutterSpeedSupport",
    "hdr",
    "hdrSupport",
    "totalSpace",
    "remainingSpace",
    "photoStitching",
    "photoStitchingSupport",
    "captureInterval",
    "captureIntervalSupport",
    "captureMode",
    "captureModeSupport",
    "exposureProgram",
    "exposureDelay",
    "exposureDelaySupport",
    "whiteBalance",
    "whiteBalanceSupport",
    "fileFormat",
    "fileFormatSupport",
    "_videoType",
    "_videoTypeSupport",
    "_topBottomCorrection",
    "_MuteEnable",
    "_batteryCapacity",
    "_sysTimestamp",
]


def _execute(client: httpx.Client, name: str, parameters: dict[str, Any] | None = None) -> Any:
    payload: dict[str, Any] = {"name": name}
    if parameters:
        payload["parameters"] = parameters
    response = client.post("/osc/commands/execute", json=payload)
    response.raise_for_status()
    return response.json()


def _poll_until_done(
    client: httpx.Client, command_id: str, *, deadline_seconds: float, interval: float = 1.0
) -> Any:
    """Poll a command to its terminal state.

    The vendor recommends about one poll per second, so the pacing here is deliberate
    rather than a tight loop. The deadline matters because a capture that never completes
    would otherwise hang the capture session indefinitely.
    """
    started = time.monotonic()
    while time.monotonic() - started < deadline_seconds:
        response = client.post("/osc/commands/status", json={"id": command_id})
        response.raise_for_status()
        body = response.json()
        state = body.get("state")
        if state in {"done", "error"}:
            return body
        time.sleep(interval)

    message = (
        f"Command {command_id} did not finish within {deadline_seconds:.0f}s. "
        "The camera may still be writing the file; nothing was left in a bad state."
    )
    raise TimeoutError(message)


def capture(base_url: str, *, take_photo: bool, timeout: float) -> dict[str, Any]:
    """Run the capture sequence and return raw, un-redacted payloads."""
    captured: dict[str, Any] = {}

    with httpx.Client(base_url=base_url, headers=HEADERS, timeout=timeout) as client:
        print("  GET  /osc/info", file=sys.stderr)
        info = client.get("/osc/info")
        info.raise_for_status()
        captured["info"] = info.json()

        print("  POST /osc/state", file=sys.stderr)
        state = client.post("/osc/state")
        state.raise_for_status()
        captured["state"] = state.json()

        print("  POST camera.getOptions", file=sys.stderr)
        captured["get_options"] = _execute(
            client, "camera.getOptions", {"optionNames": OPTION_NAMES}
        )

        # maxThumbSize is 0 on purpose: requesting thumbnails would pull base64 image data
        # from the owner's photographs across the wire for no benefit.
        print("  POST camera.listFiles (image)", file=sys.stderr)
        captured["list_files_image"] = _execute(
            client,
            "camera.listFiles",
            {"fileType": "image", "entryCount": 2, "maxThumbSize": 0},
        )

        print("  POST camera.listFiles (video)", file=sys.stderr)
        captured["list_files_video"] = _execute(
            client,
            "camera.listFiles",
            {"fileType": "video", "entryCount": 2, "maxThumbSize": 0},
        )

        if take_photo:
            print("  POST camera.takePicture", file=sys.stderr)
            acknowledgement = _execute(client, "camera.takePicture")
            captured["take_picture"] = acknowledgement

            # The acknowledgement only says the capture started. The terminal response is
            # the one carrying fileUrl and the file-group fields, and that is precisely the
            # shape the fake camera has to be checked against, so record both.
            command_id = acknowledgement.get("id")
            if command_id:
                print(f"  POST /osc/commands/status (polling {command_id})", file=sys.stderr)
                try:
                    captured["take_picture_result"] = _poll_until_done(
                        client, str(command_id), deadline_seconds=CAPTURE_DEADLINE_SECONDS
                    )
                except (TimeoutError, httpx.HTTPError) as exc:
                    print(f"  capture did not complete: {exc}", file=sys.stderr)
            else:
                print(
                    "  no command id returned; recording the acknowledgement only",
                    file=sys.stderr,
                )

    return captured


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record redacted OSC protocol fixtures from a real camera.",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument(
        "--capture-photo",
        action="store_true",
        help="Also take one picture. Off by default: this writes to the camera's storage.",
    )
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args(argv)

    print(f"Connecting to {args.base_url} ...", file=sys.stderr)
    try:
        captured = capture(args.base_url, take_photo=args.capture_photo, timeout=args.timeout)
    except httpx.HTTPError as exc:
        print(
            f"\nCould not reach the camera: {exc}\n\n"
            "Check that this machine has joined the camera's Wi-Fi access point and that "
            "the camera is powered on.",
            file=sys.stderr,
        )
        return 1

    found: set[str] = set()
    redacted = {name: redact(payload, found=found) for name, payload in captured.items()}

    info = redacted.get("info", {})
    model = str(info.get("model", "unknown")) if isinstance(info, dict) else "unknown"
    firmware = str(info.get("firmwareVersion", "unknown")) if isinstance(info, dict) else "unknown"

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in redacted.items():
        destination = args.output_dir / f"{name}.json"
        destination.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"  wrote {destination.name}", file=sys.stderr)

    manifest = {
        "camera_model": model,
        "firmware_version": firmware,
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "redacted_keys": sorted(found),
        "note": (
            "Recorded from a real camera and redacted by scripts/capture_osc_fixtures.py. "
            "Serial numbers, thumbnails and filename dates are removed or normalised. "
            "Never commit an un-redacted capture."
        ),
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(
        f"\nCaptured {model}, firmware {firmware}."
        f"\nRedacted keys: {', '.join(sorted(found)) or 'none found'}"
        f"\nReview every file before committing.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
