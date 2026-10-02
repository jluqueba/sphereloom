"""Capability vocabulary.

SphereLoom exposes one coherent tool surface no matter which backend is connected. What
differs between backends is *which capabilities are available*, and that difference is data,
not branching scattered through the tool layer.

A capability is never silently missing. It is either available, or it reports why it is not
and where to read more. See ADR-0003.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Capability(StrEnum):
    """Everything a backend might be able to do."""

    CONNECT = "connect"
    READ_INFO = "read_info"
    READ_STATUS = "read_status"
    GET_OPTIONS = "get_options"
    SET_OPTIONS = "set_options"
    SET_EXPOSURE = "set_exposure"
    TAKE_PHOTO = "take_photo"
    RECORD_VIDEO = "record_video"
    TIMELAPSE = "timelapse"
    LIST_FILES = "list_files"
    DOWNLOAD_FILE = "download_file"
    DELETE_FILE = "delete_file"
    FORMAT_STORAGE = "format_storage"
    LIVE_PREVIEW = "live_preview"
    IN_CAMERA_PHOTO_STITCH = "in_camera_photo_stitch"
    READ_MEDIA_METADATA = "read_media_metadata"
    STITCH_PHOTO = "stitch_photo"
    STITCH_VIDEO = "stitch_video"
    STABILISE_VIDEO = "stabilise_video"
    COLOUR_ADJUST = "colour_adjust"
    EXPORT_MEDIA = "export_media"


class CapabilityState(StrEnum):
    """Whether a capability can be used right now."""

    AVAILABLE = "available"
    UNSUPPORTED = "unsupported"
    NOT_IMPLEMENTED = "not_implemented"
    REQUIRES_CONFIGURATION = "requires_configuration"


class CapabilityStatus(BaseModel):
    """The answer to "can this backend do this, and if not, why not?".

    `reason`, `docs_url` and `available_in` exist so an agent can explain the limitation to
    a user and choose a different path, instead of retrying something that cannot work.
    """

    model_config = ConfigDict(frozen=True)

    capability: Capability
    state: CapabilityState
    reason: str | None = None
    docs_url: str | None = None
    available_in: str | None = None

    @property
    def is_available(self) -> bool:
        return self.state is CapabilityState.AVAILABLE
