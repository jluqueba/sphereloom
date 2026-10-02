"""Configuration.

A single settings model, validated once at startup. Validation failures name the offending
environment variable so an operator can fix the problem without reading the source.

The schema is mirrored in `.env.example`; `tests/unit/test_config.py` asserts the two stay
in sync. See `docs/features/osc-camera-control/plan.md` section 11.
"""

from __future__ import annotations

import ipaddress
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from sphereloom.domain.errors import InvalidArgumentError

ENV_PREFIX = "SPHERELOOM_"


class Transport(StrEnum):
    """How the MCP server listens for clients."""

    STDIO = "stdio"
    HTTP = "http"


class CameraBackend(StrEnum):
    """Which adapter backs the camera port."""

    OSC = "osc"
    FAKE = "fake"


class LogLevel(StrEnum):
    """Logging verbosity."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Settings(BaseSettings):
    """Every knob SphereLoom exposes, with secure defaults."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Workspace and logging
    workspace_dir: Path = Path("./sphereloom-workspace")
    log_level: LogLevel = LogLevel.INFO
    log_redact: bool = True

    # MCP transport. stdio is the default precisely because it opens no listener.
    transport: Transport = Transport.STDIO
    http_host: str = "127.0.0.1"
    http_port: int = Field(default=8765, ge=1, le=65535)
    http_token: SecretStr | None = None
    http_allow_non_loopback: bool = False

    # Camera backend
    camera_backend: CameraBackend = CameraBackend.OSC
    osc_base_url: str = "http://192.168.42.1"
    osc_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=120)
    osc_timeout_seconds: float = Field(default=15.0, gt=0, le=600)

    # Capture
    capture_timeout_seconds: float = Field(default=30.0, gt=0, le=3600)

    # Jobs
    max_concurrent_jobs: int = Field(default=2, ge=1, le=16)
    max_queued_jobs: int = Field(default=32, ge=1, le=1024)
    job_retention_count: int = Field(default=100, ge=1, le=10_000)
    job_retention_hours: int = Field(default=24, ge=1, le=720)

    # Destructive operations
    enable_destructive_tools: bool = False
    confirmation_ttl_seconds: int = Field(default=120, ge=10, le=3600)

    # Pagination
    max_page_size: int = Field(default=100, ge=1, le=1000)

    # Vendor SDK locations. Users obtain these themselves; SphereLoom never ships them.
    camera_sdk_path: Path | None = None
    media_sdk_path: Path | None = None

    # Hardware test tier
    enable_hardware_tests: bool = False

    @field_validator("osc_base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            message = (
                f"{ENV_PREFIX}OSC_BASE_URL must start with http:// or https://, got {value!r}."
            )
            raise ValueError(message)
        return value.rstrip("/")

    @model_validator(mode="after")
    def _validate_transport_guard_rails(self) -> Self:
        """Refuse to start an unauthenticated or unexpectedly exposed listener.

        These checks are deliberately part of configuration rather than the transport layer,
        so an unsafe combination can never reach a bound socket. See ADR-0002.
        """
        if self.transport is not Transport.HTTP:
            return self

        if self.http_token is None or not self.http_token.get_secret_value().strip():
            message = (
                f"{ENV_PREFIX}HTTP_TOKEN is required when {ENV_PREFIX}TRANSPORT=http. "
                "The HTTP transport must never run unauthenticated: anything that can reach "
                "the port could operate your camera and read your workspace."
            )
            raise ValueError(message)

        if not self.is_loopback_host and not self.http_allow_non_loopback:
            message = (
                f"{ENV_PREFIX}HTTP_HOST={self.http_host!r} is not a loopback address. "
                f"Binding beyond loopback exposes camera control to your network, so it "
                f"requires {ENV_PREFIX}HTTP_ALLOW_NON_LOOPBACK=true as an explicit "
                "acknowledgement of that risk."
            )
            raise ValueError(message)

        return self

    @property
    def is_loopback_host(self) -> bool:
        """Whether the configured HTTP host resolves to a loopback address."""
        if self.http_host == "localhost":
            return True
        try:
            return ipaddress.ip_address(self.http_host).is_loopback
        except ValueError:
            return False

    def resolved_workspace(self) -> Path:
        """Return the absolute workspace root, following symlinks."""
        return self.workspace_dir.expanduser().resolve()


def load_settings() -> Settings:
    """Load and validate settings, failing fast with an actionable message."""
    try:
        return Settings()
    except ValueError as exc:
        raise InvalidArgumentError(
            f"SphereLoom could not start because its configuration is invalid. {exc}".replace(
                "\n", " "
            ),
        ) from exc
