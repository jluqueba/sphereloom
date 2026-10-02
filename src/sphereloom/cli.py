"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from sphereloom import __version__
from sphereloom.config import load_settings
from sphereloom.domain.errors import SphereLoomError
from sphereloom.logging import configure_logging, get_logger
from sphereloom.server.app import build_server
from sphereloom.server.transport import run as run_transport

logger = get_logger("cli")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="sphereloom",
        description=(
            "SphereLoom MCP server. Controls 360 cameras and processes 360 media locally. "
            "Configuration is read from SPHERELOOM_* environment variables; see "
            ".env.example."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"sphereloom {__version__}",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate configuration and exit without starting the server.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the server. Returns a process exit code."""
    args = build_parser().parse_args(argv)

    try:
        settings = load_settings()
    except SphereLoomError as exc:
        # Logging is not configured yet, and stdout belongs to the MCP protocol.
        sys.stderr.write(f"{exc.message}\n")
        return 2

    configure_logging(level=settings.log_level.value, redaction=settings.log_redact)

    if args.check_config:
        logger.info("configuration is valid")
        return 0

    server = build_server(settings)
    try:
        run_transport(server, settings)
    except KeyboardInterrupt:  # pragma: no cover - interactive shutdown
        logger.info("interrupted, shutting down")
        return 130
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
