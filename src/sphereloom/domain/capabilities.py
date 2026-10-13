"""Capability vocabulary.

SphereLoom exposes one coherent tool surface no matter which backend is connected. What
differs between backends is *which capabilities are available*, and that difference is data,
not branching scattered through the tool layer.

A capability is never silently missing. It is in exactly one of three public states, and
every state other than available says why and where to read more (ADR-0003, ADR-0016). No
state names a milestone or a date.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator


class Capability(StrEnum):
    """Everything a backend might be able to do, by its public identifier."""

    CAMERA_CONNECT = "camera.connect"
    CAMERA_STATUS = "camera.status"
    OPTIONS_READ = "options.read"
    OPTIONS_WRITE = "options.write"
    PHOTO_CAPTURE = "photo.capture"
    VIDEO_RECORD = "video.record"
    FILES_LIST = "files.list"
    FILES_DOWNLOAD = "files.download"
    FILES_DELETE = "files.delete"
    EXPOSURE_CONTROL = "exposure.control"
    PREVIEW_LIVE = "preview.live"
    STORAGE_FORMAT = "storage.format"
    MEDIA_STITCH_VIDEO = "media.stitch.video"
    MEDIA_EXPORT = "media.export"


class CapabilityState(StrEnum):
    """The three public states a capability can be in (ADR-0016)."""

    AVAILABLE = "available"
    NOT_YET_AVAILABLE = "not_yet_available"
    UNSUPPORTED_BY_VENDOR = "unsupported_by_vendor"


class CapabilityStatus(BaseModel):
    """The answer to "can this backend do this, and if not, why not?".

    An unavailable capability always carries a `reason` and a `docs_url`, so an agent can
    explain the limitation to a user and choose another path instead of retrying something
    that cannot work. An available one carries neither.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: Capability
    status: CapabilityState
    reason: str | None = None
    docs_url: str | None = None

    @model_validator(mode="after")
    def _explained_when_unavailable(self) -> Self:
        if self.status is CapabilityState.AVAILABLE:
            if self.reason is not None or self.docs_url is not None:
                message = "An available capability carries no reason or documentation link."
                raise ValueError(message)
            return self
        if not self.reason or not self.docs_url:
            message = "An unavailable capability needs a reason and a documentation link."
            raise ValueError(message)
        if not self.docs_url.startswith("https://"):
            message = "A capability documentation link must be an https URL."
            raise ValueError(message)
        return self

    @property
    def is_available(self) -> bool:
        """Whether the capability can be used now."""
        return self.status is CapabilityState.AVAILABLE
