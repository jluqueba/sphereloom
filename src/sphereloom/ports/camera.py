"""The camera port.

This is the only camera contract the tool layer knows. Implementations exist per transport:
`OscCameraAdapter` over Wi-Fi (Milestone 1), a fake for tests and demos, and a USB adapter
backed by a sidecar process (Milestone 3).

Implementations must raise only `SphereLoomError` subclasses, and must report capability
gaps through `runtime_capabilities` rather than by failing opaquely.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Protocol, runtime_checkable

from sphereloom.domain.capabilities import Capability, CapabilityStatus
from sphereloom.domain.models import (
    CameraInfo,
    CameraStatus,
    CaptureResult,
    DeleteResult,
    FileQuery,
    MediaFile,
    OptionsResult,
    Page,
    RecordingHandle,
    RecordingResult,
)


@runtime_checkable
class CameraPort(Protocol):
    """Everything SphereLoom can ask of a camera."""

    @property
    def backend_name(self) -> str:
        """Short identifier reported in results and errors, for example `osc`."""
        ...

    async def connect(self) -> CameraInfo:
        """Establish and verify a session, returning the camera identity."""
        ...

    async def info(self) -> CameraInfo:
        """Return the camera identity."""
        ...

    async def status(self) -> CameraStatus:
        """Return a current reading of battery, storage and capture state."""
        ...

    async def get_options(self, names: Sequence[str] | None = None) -> OptionsResult:
        """Read settings. `None` means "everything this backend exposes"."""
        ...

    async def set_options(self, values: Mapping[str, object]) -> OptionsResult:
        """Write settings, reporting which were applied and which were rejected."""
        ...

    async def take_photo(self, *, switch_mode: bool = True) -> CaptureResult:
        """Capture a still image."""
        ...

    async def start_recording(self, *, switch_mode: bool = True) -> RecordingHandle:
        """Begin recording video."""
        ...

    async def stop_recording(self) -> RecordingResult:
        """Stop recording and return the resulting files."""
        ...

    async def list_files(self, query: FileQuery) -> Page[MediaFile]:
        """List media on the camera, newest first, with a stable cursor."""
        ...

    async def get_file(self, uri: str) -> MediaFile:
        """Return metadata for a single file."""
        ...

    def open_file_stream(self, uri: str) -> AsyncIterator[bytes]:
        """Stream a file's bytes.

        Used by the download worker. Deliberately a stream rather than a buffer: media files
        are routinely gigabytes, and they must never be materialised in memory or returned
        inside an MCP response.
        """
        ...

    async def delete_files(self, uris: Sequence[str]) -> DeleteResult:
        """Delete files. Callers above must already have validated a confirmation token."""
        ...

    async def runtime_capabilities(self) -> Mapping[Capability, CapabilityStatus]:
        """Report capability refinements discovered from the live device.

        The static table describes what a backend can do in principle; this reports what
        *this* camera, on *this* firmware, can do in practice.
        """
        ...

    async def aclose(self) -> None:
        """Release transport resources."""
        ...
