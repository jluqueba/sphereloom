"""SphereLoom: an MCP server for 360 camera control and local 360 media processing."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("sphereloom")
except PackageNotFoundError:  # pragma: no cover - only hit in an uninstalled source tree
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
