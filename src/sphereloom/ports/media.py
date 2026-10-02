"""The media port.

Declared in Milestone 1 with no implementation. Every capability it describes resolves to a
structured `unsupported` result that names the milestone which will deliver it.

This port exists now, rather than later, for a reason recorded in ADR-0003: a tool that is
absent tells an agent nothing, while a tool that explains it needs the desktop Media SDK
lets the agent tell the user something true and useful.

Scope note: the operations declared here are limited to what the vendor documents --
metadata, stitching, stabilisation, colour adjustment and export. Clip trimming, joining
separate clips and timeline editing are deliberately absent, because no vendor documentation
describes them. See `docs/vendor-capabilities.md` and ADR-0013.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable

from sphereloom.domain.capabilities import Capability, CapabilityStatus


@runtime_checkable
class MediaPort(Protocol):
    """Local processing of 360 media."""

    @property
    def backend_name(self) -> str:
        """Short identifier reported in results and errors."""
        ...

    async def read_metadata(self, path: Path) -> Mapping[str, object]:
        """Read embedded metadata from a media file."""
        ...

    async def stitch(self, source: Path, destination: Path, **options: object) -> Path:
        """Stitch dual-fisheye source media into an equirectangular output."""
        ...

    async def runtime_capabilities(self) -> Mapping[Capability, CapabilityStatus]:
        """Report what this media backend can currently do."""
        ...
