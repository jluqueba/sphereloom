"""The error taxonomy is a closed set, and this is what keeps it closed."""

from __future__ import annotations

import json

import pydantic_core
import pytest

from sphereloom.domain.bounded import JsonValue
from sphereloom.domain.errors import (
    ERROR_CLASSES,
    MAX_MESSAGE_LENGTH,
    ErrorCode,
    InternalError,
    OperationTimeoutError,
    SphereLoomError,
    StorageFullError,
    StorageUnavailableError,
    UnsupportedCapabilityError,
)


def test_every_error_code_has_exactly_one_exception_class() -> None:
    """A new code cannot be introduced without a matching exception class."""
    assert set(ERROR_CLASSES) == set(ErrorCode)


def test_every_exception_class_reports_its_own_code() -> None:
    for code, exception_class in ERROR_CLASSES.items():
        assert exception_class.code is code


def test_exception_classes_are_distinct() -> None:
    assert len(set(ERROR_CLASSES.values())) == len(ERROR_CLASSES)


def test_envelope_contains_the_required_fields() -> None:
    error = SphereLoomError("something went wrong")
    envelope = error.to_envelope()["error"]

    assert isinstance(envelope, dict)
    assert envelope["code"] == ErrorCode.INTERNAL.value
    assert envelope["message"] == "something went wrong"
    assert envelope["retryable"] is False


def test_envelope_omits_absent_optional_fields() -> None:
    envelope = SphereLoomError("plain").to_envelope()["error"]

    assert isinstance(envelope, dict)
    for absent in ("backend", "capability", "reason", "docs_url", "status", "details"):
        assert absent not in envelope


def test_unsupported_error_explains_itself() -> None:
    """An unsupported capability must say why and where to read more.

    This is the contract an agent relies on to tell a user something true instead of
    retrying an operation that cannot ever succeed.
    """
    error = UnsupportedCapabilityError(
        "Live preview is not available over Wi-Fi.",
        backend="osc",
        capability="preview.live",
        status="unsupported_by_vendor",
        reason="The camera's Wi-Fi API does not provide a live preview or stream.",
        docs_url="https://example.invalid/docs",
    )
    envelope = error.to_envelope()["error"]

    assert isinstance(envelope, dict)
    assert envelope["code"] == "unsupported"
    assert envelope["backend"] == "osc"
    assert envelope["capability"] == "preview.live"
    assert envelope["status"] == "unsupported_by_vendor"
    assert envelope["reason"]
    assert envelope["docs_url"]
    assert "available_in" not in envelope


@pytest.mark.parametrize("code", list(ErrorCode))
def test_each_code_renders_an_envelope(code: ErrorCode) -> None:
    error = ERROR_CLASSES[code]("message")
    envelope = error.to_envelope()["error"]

    assert isinstance(envelope, dict)
    assert envelope["code"] == code.value


def _envelope(error: SphereLoomError) -> dict[str, JsonValue]:
    envelope = error.to_envelope()["error"]
    assert isinstance(envelope, dict)
    return envelope


#: Whether each code is safe to repeat when the raiser knows nothing more specific.
_RETRYABLE_BY_DEFAULT = {
    ErrorCode.NOT_CONNECTED,
    ErrorCode.CAMERA_BUSY,
    ErrorCode.TIMEOUT,
    ErrorCode.RATE_LIMITED,
}


@pytest.mark.parametrize("code", list(ErrorCode))
def test_each_code_has_its_documented_default_retryability(code: ErrorCode) -> None:
    error = ERROR_CLASSES[code]("message")

    assert error.retryable is (code in _RETRYABLE_BY_DEFAULT)
    assert _envelope(error)["retryable"] is (code in _RETRYABLE_BY_DEFAULT)


def test_a_raiser_can_narrow_a_retryable_code() -> None:
    """A timeout is retryable by default, but not when the raiser says otherwise.

    This is the mechanism that lets a capture the camera already accepted be reported as
    not retryable. Applying it after acceptance belongs to the command runner, which proves
    that from the tool surface.
    """
    error = OperationTimeoutError("The capture did not finish in time.", retryable=False)

    assert error.retryable is False
    assert _envelope(error)["retryable"] is False


def test_retryability_cannot_be_widened_after_construction() -> None:
    error = InternalError("message")

    with pytest.raises(AttributeError):
        error.retryable = True  # type: ignore[misc]  # assigning a read-only property is the point

    assert _envelope(error)["retryable"] is False


@pytest.mark.parametrize(
    "code", sorted(set(ErrorCode) - _RETRYABLE_BY_DEFAULT), ids=lambda code: code.value
)
def test_retryability_cannot_be_widened(code: ErrorCode) -> None:
    error = ERROR_CLASSES[code]("message", retryable=True)  # type: ignore[arg-type]  # widening is what is being refused

    assert error.retryable is False


def test_storage_unavailable_is_a_distinct_code_from_storage_full() -> None:
    unavailable = StorageUnavailableError("No card is inserted.").to_envelope()["error"]
    full = StorageFullError("The card is full.").to_envelope()["error"]

    assert isinstance(unavailable, dict)
    assert isinstance(full, dict)
    assert unavailable["code"] == "storage_unavailable"
    assert full["code"] == "storage_full"
    assert unavailable["retryable"] is False


def test_details_passed_by_the_caller_are_kept_even_when_empty() -> None:
    details: dict[str, JsonValue] = {}

    error = InternalError("message", details=details)

    assert error.details is details


def test_the_envelope_serialises_whatever_was_passed_in() -> None:
    error = InternalError(
        "Unexpected value \ud800 from the camera.",
        reason="r" * 10_000,
        details={
            "nan": float("nan"),
            "huge": 10**5000,
            "surrogate": "\udfff",
            "set": {1, 2},  # type: ignore[dict-item]  # a value outside the declared type is the point
            "nested": [[[[[["deep"]]]]]],
        },
    )

    envelope = error.to_envelope()

    json.dumps(envelope, allow_nan=False).encode("utf-8")
    pydantic_core.to_json(envelope)


def test_the_envelope_message_is_bounded() -> None:
    envelope = InternalError("m" * 100_000).to_envelope()["error"]

    assert isinstance(envelope, dict)
    message = envelope["message"]
    assert isinstance(message, str)
    assert len(message) <= MAX_MESSAGE_LENGTH + 1
