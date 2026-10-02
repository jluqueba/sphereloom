"""Configuration must fail fast, name the offending variable, and never open an unsafe port."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from sphereloom.config import ENV_PREFIX, CameraBackend, Settings, Transport, load_settings
from sphereloom.domain.errors import InvalidArgumentError

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def test_defaults_are_the_safe_ones(settings: Settings) -> None:
    """The defaults a user gets without configuring anything must be the cautious ones."""
    assert settings.transport is Transport.STDIO
    assert settings.camera_backend is CameraBackend.OSC
    assert settings.enable_destructive_tools is False
    assert settings.log_redact is True
    assert settings.http_allow_non_loopback is False


def test_http_transport_refuses_to_start_without_a_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPHERELOOM_TRANSPORT", "http")

    with pytest.raises(ValidationError, match="SPHERELOOM_HTTP_TOKEN"):
        Settings()


def test_http_transport_refuses_a_blank_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPHERELOOM_TRANSPORT", "http")
    monkeypatch.setenv("SPHERELOOM_HTTP_TOKEN", "   ")

    with pytest.raises(ValidationError, match="SPHERELOOM_HTTP_TOKEN"):
        Settings()


def test_http_transport_accepts_a_token_on_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPHERELOOM_TRANSPORT", "http")
    monkeypatch.setenv("SPHERELOOM_HTTP_TOKEN", "a-real-token")

    loaded = Settings()

    assert loaded.transport is Transport.HTTP
    assert loaded.is_loopback_host is True


def test_binding_beyond_loopback_requires_explicit_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exposing camera control to a network must be a deliberate act, not a typo."""
    monkeypatch.setenv("SPHERELOOM_TRANSPORT", "http")
    monkeypatch.setenv("SPHERELOOM_HTTP_TOKEN", "a-real-token")
    monkeypatch.setenv("SPHERELOOM_HTTP_HOST", "0.0.0.0")

    with pytest.raises(ValidationError, match="SPHERELOOM_HTTP_ALLOW_NON_LOOPBACK"):
        Settings()


def test_non_loopback_is_allowed_once_acknowledged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPHERELOOM_TRANSPORT", "http")
    monkeypatch.setenv("SPHERELOOM_HTTP_TOKEN", "a-real-token")
    monkeypatch.setenv("SPHERELOOM_HTTP_HOST", "0.0.0.0")
    monkeypatch.setenv("SPHERELOOM_HTTP_ALLOW_NON_LOOPBACK", "true")

    loaded = Settings()

    assert loaded.is_loopback_host is False


def test_a_bad_base_url_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPHERELOOM_OSC_BASE_URL", "192.168.42.1")

    with pytest.raises(ValidationError, match="SPHERELOOM_OSC_BASE_URL"):
        Settings()


def test_base_url_trailing_slash_is_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPHERELOOM_OSC_BASE_URL", "http://192.168.42.1/")

    assert Settings().osc_base_url == "http://192.168.42.1"


def test_load_settings_wraps_failures_in_the_taxonomy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPHERELOOM_TRANSPORT", "http")

    with pytest.raises(InvalidArgumentError) as caught:
        load_settings()

    assert "SPHERELOOM_HTTP_TOKEN" in caught.value.message


def test_the_token_is_not_exposed_by_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    """A settings dump lands in bug reports, so the token must not be readable in one."""
    monkeypatch.setenv("SPHERELOOM_TRANSPORT", "http")
    monkeypatch.setenv("SPHERELOOM_HTTP_TOKEN", "super-secret-value")

    assert "super-secret-value" not in repr(Settings())


def test_env_example_documents_every_setting() -> None:
    """`.env.example` is documentation, and documentation that drifts is worse than none."""
    documented = {
        match.group(1)
        for match in re.finditer(r"^#?\s*(SPHERELOOM_[A-Z0-9_]+)=", ENV_EXAMPLE.read_text(), re.M)
    }
    expected = {
        f"{ENV_PREFIX}{name.upper()}"
        for name in Settings.model_fields
        # Agent variables belong to the example agent, not to the server settings model.
    }

    missing = expected - documented
    assert not missing, f".env.example is missing: {sorted(missing)}"


def test_env_example_documents_nothing_unknown() -> None:
    documented = {
        match.group(1)
        for match in re.finditer(r"^#?\s*(SPHERELOOM_[A-Z0-9_]+)=", ENV_EXAMPLE.read_text(), re.M)
    }
    known = {f"{ENV_PREFIX}{name.upper()}" for name in Settings.model_fields}
    # The example agent (Milestone 2) reads its own variables, which the server ignores.
    agent_variables = {name for name in documented if name.startswith(f"{ENV_PREFIX}AGENT_")}

    unknown = documented - known - agent_variables
    assert not unknown, f".env.example documents variables that do not exist: {sorted(unknown)}"
