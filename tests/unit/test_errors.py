"""The error taxonomy is a closed set, and this is what keeps it closed."""

from __future__ import annotations

import pytest

from sphereloom.domain.errors import (
    ERROR_CLASSES,
    ErrorCode,
    SphereLoomError,
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
    for absent in ("backend", "capability", "reason", "docs_url", "available_in", "details"):
        assert absent not in envelope


def test_unsupported_error_explains_itself() -> None:
    """An unsupported capability must say why and where to read more.

    This is the contract an agent relies on to tell a user something true instead of
    retrying an operation that cannot ever succeed.
    """
    error = UnsupportedCapabilityError(
        "Live preview is not available over Wi-Fi.",
        backend="osc",
        capability="live_preview",
        reason="The vendor documents that the OSC protocol provides no real-time feed.",
        docs_url="https://example.invalid/docs",
        available_in="M3",
    )
    envelope = error.to_envelope()["error"]

    assert isinstance(envelope, dict)
    assert envelope["code"] == "unsupported"
    assert envelope["backend"] == "osc"
    assert envelope["capability"] == "live_preview"
    assert envelope["reason"]
    assert envelope["docs_url"]
    assert envelope["available_in"] == "M3"


@pytest.mark.parametrize("code", list(ErrorCode))
def test_each_code_renders_an_envelope(code: ErrorCode) -> None:
    error = ERROR_CLASSES[code]("message")
    envelope = error.to_envelope()["error"]

    assert isinstance(envelope, dict)
    assert envelope["code"] == code.value
