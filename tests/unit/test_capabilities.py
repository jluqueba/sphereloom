"""The capability registry: three public states, honest data, and a check before any I/O.

AC-2.1.1 to AC-2.2.3, ADR-0003, ADR-0016.
"""

from __future__ import annotations

import json
import re
from collections.abc import Coroutine

import pytest
from pydantic import ValidationError

from sphereloom.capabilities.data import STATIC_CAPABILITIES
from sphereloom.capabilities.registry import CapabilityRegistry, guarded
from sphereloom.domain.capabilities import Capability, CapabilityState, CapabilityStatus
from sphereloom.domain.errors import InternalError, UnsupportedCapabilityError

_PLANNING = re.compile(r"(?i)\bmilestones?\b|\bM[0-9](?:\.[0-9])?\b|\broadmap\b|\b20[0-9]{2}\b")

AVAILABLE = CapabilityStatus(capability=Capability.CAMERA_CONNECT, status=CapabilityState.AVAILABLE)


def _narrowed(capability: Capability) -> CapabilityStatus:
    return CapabilityStatus(
        capability=capability,
        status=CapabilityState.NOT_YET_AVAILABLE,
        reason="This camera model does not report it.",
        docs_url="https://example.invalid/model",
    )


def _registry_with_connect_available() -> CapabilityRegistry:
    table = dict(STATIC_CAPABILITIES["osc"])
    table[Capability.CAMERA_CONNECT] = AVAILABLE
    return CapabilityRegistry("osc", {"osc": table})


@pytest.mark.parametrize("backend", sorted(STATIC_CAPABILITIES))
def test_every_capability_resolves_to_exactly_one_state(backend: str) -> None:
    statuses = CapabilityRegistry(backend).resolve_all()

    assert [status.capability for status in statuses] == list(Capability)
    assert {status.status for status in statuses} <= set(CapabilityState)


def test_the_fake_camera_declares_what_the_real_one_does() -> None:
    """The fake speaks the same Wi-Fi protocol, so a demo never shows more than a camera can."""
    assert CapabilityRegistry("fake").resolve_all() == CapabilityRegistry("osc").resolve_all()


def test_the_identifiers_are_exactly_those_of_the_plan() -> None:
    assert {capability.value for capability in Capability} == {
        "camera.connect",
        "camera.status",
        "options.read",
        "options.write",
        "photo.capture",
        "video.record",
        "files.list",
        "files.download",
        "files.delete",
        "exposure.control",
        "preview.live",
        "storage.format",
        "media.stitch.video",
        "media.export",
    }


@pytest.mark.parametrize(
    "capability",
    [Capability.EXPOSURE_CONTROL, Capability.PREVIEW_LIVE, Capability.STORAGE_FORMAT],
)
def test_vendor_gaps_report_unsupported_by_vendor(capability: Capability) -> None:
    """AC-2.1.2: the Wi-Fi API offers none of these."""
    status = CapabilityRegistry("osc").resolve(capability)

    assert status.status is CapabilityState.UNSUPPORTED_BY_VENDOR


@pytest.mark.parametrize("capability", [Capability.MEDIA_STITCH_VIDEO, Capability.MEDIA_EXPORT])
def test_media_processing_reports_not_yet_available(capability: Capability) -> None:
    """AC-2.1.3."""
    status = CapabilityRegistry("osc").resolve(capability)

    assert status.status is CapabilityState.NOT_YET_AVAILABLE


@pytest.mark.parametrize("status", CapabilityRegistry("osc").resolve_all(), ids=str)
def test_no_reason_names_a_milestone_or_a_date(status: CapabilityStatus) -> None:
    """ADR-0016: tool output describes the current state only."""
    assert not _PLANNING.search(status.reason or "")


async def test_an_unavailable_capability_is_refused_before_the_operation_is_created() -> None:
    """AC-2.2.2: the check happens before any I/O, so the camera is never touched."""
    created = 0

    async def operation() -> str:
        return "touched the camera"

    def factory() -> Coroutine[None, None, str]:
        nonlocal created
        created += 1
        return operation()

    with pytest.raises(UnsupportedCapabilityError):
        await guarded(CapabilityRegistry("osc"), Capability.PREVIEW_LIVE, factory)

    assert created == 0


async def test_an_available_capability_runs_the_operation_once() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "done"

    result = await guarded(_registry_with_connect_available(), Capability.CAMERA_CONNECT, operation)

    assert result == "done"
    assert calls == 1


def test_the_refusal_is_the_canonical_unsupported_envelope() -> None:
    """AC-2.2.1."""
    with pytest.raises(UnsupportedCapabilityError) as caught:
        CapabilityRegistry("osc").require(Capability.EXPOSURE_CONTROL)

    envelope = caught.value.to_envelope()
    error = envelope["error"]
    assert isinstance(error, dict)
    assert error["code"] == "unsupported"
    assert error["backend"] == "osc"
    assert error["capability"] == "exposure.control"
    assert error["status"] == "unsupported_by_vendor"
    assert error["reason"]
    assert str(error["docs_url"]).startswith("https://")
    assert error["retryable"] is False
    assert "available_in" not in error
    json.dumps(envelope, allow_nan=False)


def test_runtime_information_can_narrow_an_available_capability() -> None:
    registry = _registry_with_connect_available()
    runtime = {Capability.CAMERA_CONNECT: _narrowed(Capability.CAMERA_CONNECT)}

    status = registry.resolve(Capability.CAMERA_CONNECT, runtime)

    assert status.status is CapabilityState.NOT_YET_AVAILABLE
    with pytest.raises(UnsupportedCapabilityError):
        registry.require(Capability.CAMERA_CONNECT, runtime)


@pytest.mark.parametrize(
    "capability",
    [Capability.PREVIEW_LIVE, Capability.MEDIA_EXPORT],
    ids=["vendor-gap", "not-yet-available"],
)
def test_runtime_information_can_never_widen_a_capability(capability: Capability) -> None:
    widened = CapabilityStatus(capability=capability, status=CapabilityState.AVAILABLE)

    status = CapabilityRegistry("osc").resolve(capability, {capability: widened})

    assert not status.is_available


def test_runtime_information_cannot_rewrite_a_declared_gap() -> None:
    """A device cannot turn a vendor gap into "not yet", nor replace its reason or link."""
    declared = CapabilityRegistry("osc").resolve(Capability.PREVIEW_LIVE)

    status = CapabilityRegistry("osc").resolve(
        Capability.PREVIEW_LIVE, {Capability.PREVIEW_LIVE: _narrowed(Capability.PREVIEW_LIVE)}
    )

    assert status == declared


def test_an_available_refinement_leaves_the_declaration_unchanged() -> None:
    registry = _registry_with_connect_available()

    status = registry.resolve(Capability.CAMERA_CONNECT, {Capability.CAMERA_CONNECT: AVAILABLE})

    assert status == AVAILABLE


def test_a_refinement_filed_under_another_capability_narrows_only_its_key() -> None:
    registry = _registry_with_connect_available()
    misfiled = {Capability.CAMERA_CONNECT: _narrowed(Capability.PREVIEW_LIVE)}

    status = registry.resolve(Capability.CAMERA_CONNECT, misfiled)

    assert status.capability is Capability.CAMERA_CONNECT
    assert not status.is_available


def test_an_unknown_backend_is_an_internal_error() -> None:
    with pytest.raises(InternalError):
        CapabilityRegistry("usb")


def test_an_incomplete_declaration_is_an_internal_error() -> None:
    table = dict(STATIC_CAPABILITIES["osc"])
    del table[Capability.MEDIA_EXPORT]

    with pytest.raises(InternalError):
        CapabilityRegistry("osc", {"osc": table})


def test_a_status_declared_under_another_identifier_is_an_internal_error() -> None:
    table = dict(STATIC_CAPABILITIES["osc"])
    table[Capability.CAMERA_CONNECT] = STATIC_CAPABILITIES["osc"][Capability.PREVIEW_LIVE]

    with pytest.raises(InternalError):
        CapabilityRegistry("osc", {"osc": table})


def test_a_device_supplied_reason_never_reaches_the_message() -> None:
    """A runtime reason comes from the camera; only the bounded `reason` field carries it."""
    registry = _registry_with_connect_available()
    long_reason = "device text " * 10_000
    runtime = {
        Capability.CAMERA_CONNECT: CapabilityStatus(
            capability=Capability.CAMERA_CONNECT,
            status=CapabilityState.NOT_YET_AVAILABLE,
            reason=long_reason,
            docs_url="https://example.invalid/model",
        )
    }

    with pytest.raises(UnsupportedCapabilityError) as caught:
        registry.require(Capability.CAMERA_CONNECT, runtime)

    assert "device text" not in caught.value.message
    rendered = caught.value.to_envelope()["error"]
    assert isinstance(rendered, dict)
    assert len(str(rendered["reason"])) < len(long_reason)


@pytest.mark.parametrize(
    "fields",
    [
        {"status": CapabilityState.AVAILABLE, "reason": "works"},
        {"status": CapabilityState.AVAILABLE, "docs_url": "https://example.invalid"},
        {"status": CapabilityState.NOT_YET_AVAILABLE},
        {"status": CapabilityState.NOT_YET_AVAILABLE, "reason": "later"},
        {"status": CapabilityState.UNSUPPORTED_BY_VENDOR, "docs_url": "https://example.invalid"},
        {
            "status": CapabilityState.UNSUPPORTED_BY_VENDOR,
            "reason": "",
            "docs_url": "https://x.invalid",
        },
        {
            "status": CapabilityState.NOT_YET_AVAILABLE,
            "reason": "r",
            "docs_url": "http://x.invalid",
        },
        {"status": "requires_milestone", "reason": "r", "docs_url": "https://x.invalid"},
    ],
    ids=[
        "available-with-reason",
        "available-with-link",
        "unavailable-unexplained",
        "unavailable-without-link",
        "unavailable-without-reason",
        "unavailable-with-empty-reason",
        "insecure-link",
        "fourth-state",
    ],
)
def test_a_status_is_explained_exactly_when_unavailable(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CapabilityStatus(capability=Capability.CAMERA_CONNECT, **fields)  # type: ignore[arg-type]  # invalid input is the point
