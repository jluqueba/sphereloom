"""Domain models.

These are the vocabulary the tool layer speaks. Adapters translate vendor payloads into
these types, so no vendor-specific shape ever reaches a tool.

Every vendor-supplied optional field is genuinely optional here: a camera that omits a
field yields `None` rather than an exception, because firmware revisions differ and a
missing battery reading is not a reason to fail a status call.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MediaKind(StrEnum):
    """What sort of media a file holds."""

    PHOTO = "photo"
    VIDEO = "video"


class CaptureMode(StrEnum):
    """The camera's current shooting mode."""

    IMAGE = "image"
    VIDEO = "video"
    UNKNOWN = "unknown"


class CameraInfo(BaseModel):
    """Identity of the connected camera."""

    model_config = ConfigDict(frozen=True)

    model: str
    serial_number: str | None = None
    firmware_version: str | None = None
    api_levels: tuple[int, ...] = ()
    base_url: str | None = None


class CameraStatus(BaseModel):
    """A point-in-time reading of the camera.

    `observed_at` and `age_seconds` are reported because some of these values are cached to
    respect the vendor's polling guidance; an agent deciding whether to start a recording
    deserves to know how fresh the battery reading is.
    """

    model_config = ConfigDict(frozen=True)

    battery_percent: int | None = Field(default=None, ge=0, le=100)
    charging: bool | None = None
    storage_free_bytes: int | None = Field(default=None, ge=0)
    storage_total_bytes: int | None = Field(default=None, ge=0)
    remaining_video_seconds: int | None = Field(default=None, ge=0)
    remaining_photos: int | None = Field(default=None, ge=0)
    capture_mode: CaptureMode = CaptureMode.UNKNOWN
    capture_in_progress: bool = False
    capture_elapsed_seconds: float | None = Field(default=None, ge=0)
    observed_at: datetime
    age_seconds: float = Field(default=0.0, ge=0)


class OptionDescriptor(BaseModel):
    """A single camera setting and what may be done with it."""

    model_config = ConfigDict(frozen=True)

    name: str
    value: str | int | float | bool | None = None
    value_type: Literal["string", "integer", "number", "boolean", "unknown"] = "unknown"
    supported_values: tuple[str | int | float | bool, ...] | None = None
    writable: bool = True
    # Vendor extensions are the underscore-prefixed options outside the OSC specification.
    # They are surfaced rather than hidden, but flagged so callers know they are non-standard.
    vendor_extension: bool = False


class OptionsResult(BaseModel):
    """The outcome of reading or writing settings."""

    model_config = ConfigDict(frozen=True)

    options: tuple[OptionDescriptor, ...] = ()
    applied: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


class MediaFile(BaseModel):
    """A file on the camera, or a file already downloaded into the workspace.

    `stitched` matters: footage straight off the camera is dual-fisheye and is not a viewable
    360 video until it is stitched. Callers must be able to tell the difference.
    """

    model_config = ConfigDict(frozen=True)

    uri: str
    name: str
    kind: MediaKind
    size_bytes: int | None = Field(default=None, ge=0)
    captured_at: datetime | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    group_id: str | None = None
    stitched: bool = False
    local_path: str | None = None


class FileQuery(BaseModel):
    """Filters for a gallery listing."""

    model_config = ConfigDict(frozen=True)

    kind: MediaKind | None = None
    limit: int = Field(default=25, ge=1, le=1000)
    cursor: str | None = None


class CaptureResult(BaseModel):
    """What a photo capture produced."""

    model_config = ConfigDict(frozen=True)

    files: tuple[MediaFile, ...] = ()
    mode_switched: bool = False


class RecordingHandle(BaseModel):
    """Acknowledgement that recording started."""

    model_config = ConfigDict(frozen=True)

    started_at: datetime
    mode_switched: bool = False


class RecordingResult(BaseModel):
    """What a finished recording produced.

    A single recording can yield several files, which is why `files` is a collection.
    """

    model_config = ConfigDict(frozen=True)

    files: tuple[MediaFile, ...] = ()
    duration_seconds: float | None = Field(default=None, ge=0)
    group_id: str | None = None


class DeleteResult(BaseModel):
    """Which deletions succeeded and which did not."""

    model_config = ConfigDict(frozen=True)

    deleted: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()


class Page[T](BaseModel):
    """One page of results.

    The cursor is opaque by contract: callers pass it back unchanged and must not parse it.
    """

    model_config = ConfigDict(frozen=True)

    items: tuple[T, ...] = ()
    next_cursor: str | None = None
    total_estimate: int | None = Field(default=None, ge=0)
