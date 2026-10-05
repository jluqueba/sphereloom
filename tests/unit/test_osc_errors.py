"""Vendor error payloads must land on the taxonomy, never escape as raw exceptions.

An agent branches on `code`. If an unrecognised vendor condition reached it as a crash, or
as a different code each firmware revision, that contract would be worthless.
"""

from __future__ import annotations

import pytest

from sphereloom.adapters.osc.errors import map_vendor_error
from sphereloom.domain.errors import (
    CameraBusyError,
    ErrorCode,
    InternalError,
    InvalidArgumentError,
    NotFoundError,
    StorageFullError,
)


def _error(code: str, message: str = "something happened") -> dict[str, object]:
    return {
        "name": "camera.takePicture",
        "state": "error",
        "error": {"code": code, "message": message},
    }


@pytest.mark.parametrize(
    ("vendor_code", "expected"),
    [
        ("disabledCommand", CameraBusyError),
        ("invalidParameterName", InvalidArgumentError),
        ("invalidParameterValue", InvalidArgumentError),
        ("missingParameter", InvalidArgumentError),
        ("noFreeSpace", StorageFullError),
        ("fileNotFound", NotFoundError),
        ("powerOffSequenceRunning", CameraBusyError),
    ],
)
def test_documented_vendor_codes_map_to_the_taxonomy(
    vendor_code: str, expected: type[Exception]
) -> None:
    assert isinstance(map_vendor_error(_error(vendor_code)), expected)


def test_an_unknown_vendor_code_becomes_internal_rather_than_a_guess() -> None:
    """Inventing a mapping for an unseen code would be worse than admitting ignorance."""
    mapped = map_vendor_error(_error("somethingNewInFirmware"))

    assert isinstance(mapped, InternalError)
    assert "somethingNewInFirmware" in mapped.message


def test_the_original_vendor_payload_is_preserved() -> None:
    """A maintainer debugging real hardware must not be left guessing what it said."""
    mapped = map_vendor_error(_error("noFreeSpace", "card is full"))

    assert mapped.details["vendor"] == _error("noFreeSpace", "card is full")
    assert mapped.reason == "card is full"


def test_the_command_name_is_recorded() -> None:
    mapped = map_vendor_error(_error("disabledCommand"), command="camera.startCapture")

    assert mapped.details["command"] == "camera.startCapture"
    assert "camera.startCapture" in mapped.message


def test_an_unactivated_camera_tells_the_user_what_only_they_can_do() -> None:
    """Nothing SphereLoom does recovers from this, so the instruction must survive."""
    mapped = map_vendor_error(_error("unactivated", "Please activate your camera."))

    assert mapped.code is ErrorCode.INVALID_ARGUMENT
    assert "activate" in mapped.message.lower()
    assert "app" in mapped.message.lower()


def test_guidance_is_attached_to_known_conditions() -> None:
    """The message has to say what to do next, not only what went wrong."""
    mapped = map_vendor_error(_error("noFreeSpace"))

    assert "free space" in mapped.message.lower()


def test_a_payload_with_no_error_object_is_reported_not_crashed() -> None:
    mapped = map_vendor_error({"unexpected": "shape"})

    assert isinstance(mapped, InternalError)


def test_a_non_dict_payload_is_reported_not_crashed() -> None:
    assert isinstance(map_vendor_error("not json at all"), InternalError)


def test_a_nested_error_is_found() -> None:
    """The vendor nests the error differently depending on how the command failed."""
    payload = {
        "name": "camera.delete",
        "state": "error",
        "results": {"error": {"code": "fileNotFound", "message": "gone"}},
    }

    assert isinstance(map_vendor_error(payload), NotFoundError)


def test_a_long_vendor_string_is_truncated() -> None:
    """A stray thumbnail or file path must not escape through an error detail."""
    mapped = map_vendor_error(_error("noFreeSpace", "x" * 5000))

    vendor = mapped.details["vendor"]
    assert isinstance(vendor, dict)
    error = vendor["error"]
    assert isinstance(error, dict)
    assert len(str(error["message"])) < 300


def test_every_mapped_error_carries_the_backend() -> None:
    """Results and errors name the backend so an agent can explain which path failed."""
    for code in ("disabledCommand", "noFreeSpace", "unknownToUs"):
        assert map_vendor_error(_error(code)).backend == "osc"
