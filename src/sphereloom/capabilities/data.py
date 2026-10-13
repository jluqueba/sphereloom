"""Static capability declarations per backend.

This table is data, reviewed like code: each change to a state is a visible one-line diff,
and each vendor gap cites vendor documentation. It describes what SphereLoom provides
today, so a capability becomes available in the same change that delivers it, and the
public matrix never claims more than exists. Reasons are written in the present tense and
never name a milestone or a date (ADR-0016).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from sphereloom.domain.capabilities import Capability, CapabilityState, CapabilityStatus

#: The public capability page, linked from every capability SphereLoom does not offer yet.
CAPABILITIES_PAGE = "https://jluqueba.github.io/sphereloom/docs/CAPABILITIES.html"

#: The vendor's integration FAQ, which states that the Wi-Fi API offers neither live preview
#: nor exposure control.
VENDOR_INTEGRATION_FAQ = "https://onlinemanual.insta360.com/developer/en-us/resource/integration"

#: The vendor's Wi-Fi API reference, whose command list has no storage format command.
VENDOR_OSC_REFERENCE = "https://www.insta360.com/us/developer/tutorial?type=osc"

_NOT_YET = "SphereLoom does not provide this over Wi-Fi yet."
_NEEDS_MEDIA_SDK = (
    "SphereLoom does not provide this yet. It needs the Insta360 Media SDK, which you obtain "
    "from the vendor yourself, and an NVIDIA GPU."
)


def _not_yet(capability: Capability, reason: str = _NOT_YET) -> CapabilityStatus:
    return CapabilityStatus(
        capability=capability,
        status=CapabilityState.NOT_YET_AVAILABLE,
        reason=reason,
        docs_url=CAPABILITIES_PAGE,
    )


def _vendor_gap(capability: Capability, reason: str, docs_url: str) -> CapabilityStatus:
    return CapabilityStatus(
        capability=capability,
        status=CapabilityState.UNSUPPORTED_BY_VENDOR,
        reason=reason,
        docs_url=docs_url,
    )


_OSC: dict[Capability, CapabilityStatus] = {
    Capability.CAMERA_CONNECT: _not_yet(Capability.CAMERA_CONNECT),
    Capability.CAMERA_STATUS: _not_yet(Capability.CAMERA_STATUS),
    Capability.OPTIONS_READ: _not_yet(Capability.OPTIONS_READ),
    Capability.OPTIONS_WRITE: _not_yet(Capability.OPTIONS_WRITE),
    Capability.PHOTO_CAPTURE: _not_yet(Capability.PHOTO_CAPTURE),
    Capability.VIDEO_RECORD: _not_yet(Capability.VIDEO_RECORD),
    Capability.FILES_LIST: _not_yet(Capability.FILES_LIST),
    Capability.FILES_DOWNLOAD: _not_yet(Capability.FILES_DOWNLOAD),
    Capability.FILES_DELETE: _not_yet(Capability.FILES_DELETE),
    Capability.EXPOSURE_CONTROL: _vendor_gap(
        Capability.EXPOSURE_CONTROL,
        "The camera's Wi-Fi API does not expose exposure or white balance parameters.",
        VENDOR_INTEGRATION_FAQ,
    ),
    Capability.PREVIEW_LIVE: _vendor_gap(
        Capability.PREVIEW_LIVE,
        "The camera's Wi-Fi API does not provide a live preview or stream.",
        VENDOR_INTEGRATION_FAQ,
    ),
    Capability.STORAGE_FORMAT: _vendor_gap(
        Capability.STORAGE_FORMAT,
        "The camera's Wi-Fi API has no command to format the storage card.",
        VENDOR_OSC_REFERENCE,
    ),
    Capability.MEDIA_STITCH_VIDEO: _not_yet(Capability.MEDIA_STITCH_VIDEO, _NEEDS_MEDIA_SDK),
    Capability.MEDIA_EXPORT: _not_yet(Capability.MEDIA_EXPORT, _NEEDS_MEDIA_SDK),
}

#: Every backend's declarations, keyed by the backend name reported in results and errors.
#: The fake camera speaks the same Wi-Fi protocol, so it is served by the `osc` entry.
STATIC_CAPABILITIES: Mapping[str, Mapping[Capability, CapabilityStatus]] = MappingProxyType(
    {"osc": MappingProxyType(_OSC)}
)
