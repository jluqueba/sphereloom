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


@pytest.mark.parametrize(
    "url",
    [
        "192.168.42.1",
        "ftp://192.168.42.1",
        "http://",
        "http://192.168.42.1:not-a-port",
        "http://192.168.42.1:99999",
        "http://operator:secret@192.168.42.1",
        "http://192.168.42.1/?token=secret",
        "http://192.168.42.1/#fragment",
    ],
    ids=[
        "no-scheme",
        "wrong-scheme",
        "no-host",
        "non-numeric-port",
        "port-out-of-range",
        "credentials",
        "query",
        "fragment",
    ],
)
def test_a_malformed_camera_address_fails_at_startup_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    """AC-9.6: the error names the variable, and the address never reaches the camera client."""
    monkeypatch.setenv("SPHERELOOM_OSC_BASE_URL", url)

    with pytest.raises(InvalidArgumentError, match="SPHERELOOM_OSC_BASE_URL"):
        load_settings()


@pytest.mark.parametrize(
    "url", ["http://192.168.42.1", "http://127.0.0.1:8080/", "https://camera.local/osc-root"]
)
def test_a_well_formed_camera_address_is_accepted(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("SPHERELOOM_OSC_BASE_URL", url)

    assert load_settings().osc_base_url == url.rstrip("/")


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


SECRET = "s3cret-value-under-test"


@pytest.mark.parametrize(
    ("variable", "value", "extra"),
    [
        ("SPHERELOOM_OSC_BASE_URL", f"http://operator:{SECRET}@192.168.42.1", {}),
        ("SPHERELOOM_OSC_BASE_URL", f"http://192.168.42.1/?token={SECRET}", {}),
        (
            "SPHERELOOM_HTTP_TOKEN",
            SECRET,
            {"SPHERELOOM_TRANSPORT": "http", "SPHERELOOM_HTTP_HOST": "0.0.0.0"},
        ),
    ],
    ids=["url-credentials", "url-query", "http-token"],
)
def test_a_configuration_error_never_repeats_an_input_value(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str, extra: dict[str, str]
) -> None:
    """The validation error records every input; neither its text nor the error is passed on."""
    monkeypatch.setenv(variable, value)
    for name, setting in extra.items():
        monkeypatch.setenv(name, setting)

    with pytest.raises(InvalidArgumentError) as caught:
        load_settings()

    assert SECRET not in caught.value.message
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "SPHERELOOM_" in caught.value.message


@pytest.mark.parametrize("url", ["http://[", "http://camera:", "http://camera:/osc"])
def test_an_unparseable_or_empty_port_address_names_the_variable(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("SPHERELOOM_OSC_BASE_URL", url)

    with pytest.raises(InvalidArgumentError, match="SPHERELOOM_OSC_BASE_URL must be"):
        load_settings()
