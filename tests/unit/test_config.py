"""Tests for api.config (contract: specs/001-walking-skeleton/contracts/configuration.md)."""

import logging

import pytest
from pydantic import SecretStr, ValidationError

from api.config import Settings, get_settings, load_settings

PRIVATE_DSN = "postgresql://disclosly:s3cr3t-value@postgres:5432/disclosly"

ENV_VARS = (
    "APP_ENV",
    "APP_VERSION",
    "DATABASE_URL",
    "REDIS_URL",
    "CORS_ALLOWED_ORIGINS",
    "LOG_LEVEL",
    "PROMETHEUS_MULTIPROC_DIR",
    "JWT_SECRET",
    "WEB_ORIGIN",
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A clean, valid production-like environment."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", PRIVATE_DSN)
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://disclosly.com, https://www.disclosly.com")
    monkeypatch.setenv("JWT_SECRET", "jwt-secret-value-that-is-long-enough-123")
    return monkeypatch


def test_loads_valid_environment(env: pytest.MonkeyPatch) -> None:
    settings = Settings()

    assert settings.app_env == "production"
    assert isinstance(settings.database_url, SecretStr)
    assert settings.database_url.get_secret_value() == PRIVATE_DSN
    assert settings.redis_url == "redis://redis:6379/0"
    assert settings.cors_allowed_origins == [
        "https://disclosly.com",
        "https://www.disclosly.com",
    ]


def test_defaults_for_optional_fields(env: pytest.MonkeyPatch) -> None:
    settings = Settings()

    assert settings.app_version == "dev"
    assert settings.log_level == "INFO"
    assert settings.prometheus_multiproc_dir is None


@pytest.mark.parametrize("value", ["development", "test", "production"])
def test_app_env_accepts_known_values(env: pytest.MonkeyPatch, value: str) -> None:
    env.setenv("APP_ENV", value)
    assert Settings().app_env == value


def test_app_env_rejects_unknown_value(env: pytest.MonkeyPatch) -> None:
    env.setenv("APP_ENV", "staging")
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize(
    "missing", ["APP_ENV", "DATABASE_URL", "REDIS_URL", "CORS_ALLOWED_ORIGINS"]
)
def test_required_variables(env: pytest.MonkeyPatch, missing: str) -> None:
    env.delenv(missing)
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize(
    "origins",
    [
        "http://disclosly.com",
        "disclosly.com",
        "https://disclosly.com/path",
        "https://disclosly.com?x=1",
        "ftp://disclosly.com",
        "https://",
        "",
        "http://localhost:3000",
    ],
)
def test_cors_rejects_invalid_origins_in_production(env: pytest.MonkeyPatch, origins: str) -> None:
    env.setenv("CORS_ALLOWED_ORIGINS", origins)
    with pytest.raises(ValidationError):
        Settings()


def test_cors_allows_https_origin_with_port(env: pytest.MonkeyPatch) -> None:
    env.setenv("CORS_ALLOWED_ORIGINS", "https://disclosly.com:8443")
    assert Settings().cors_allowed_origins == ["https://disclosly.com:8443"]


def test_cors_strips_trailing_slash(env: pytest.MonkeyPatch) -> None:
    env.setenv("CORS_ALLOWED_ORIGINS", "https://disclosly.com/")
    assert Settings().cors_allowed_origins == ["https://disclosly.com"]


@pytest.mark.parametrize("origin", ["http://localhost:3000", "http://localhost"])
def test_cors_allows_http_localhost_in_development(env: pytest.MonkeyPatch, origin: str) -> None:
    env.setenv("APP_ENV", "development")
    env.setenv("CORS_ALLOWED_ORIGINS", f"{origin},https://disclosly.com")
    assert Settings().cors_allowed_origins == [origin, "https://disclosly.com"]


@pytest.mark.parametrize("app_env", ["test", "production"])
def test_cors_rejects_http_localhost_outside_development(
    env: pytest.MonkeyPatch, app_env: str
) -> None:
    env.setenv("APP_ENV", app_env)
    env.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
    with pytest.raises(ValidationError):
        Settings()


def test_cors_rejects_http_non_localhost_in_development(env: pytest.MonkeyPatch) -> None:
    env.setenv("APP_ENV", "development")
    env.setenv("CORS_ALLOWED_ORIGINS", "http://example.com")
    with pytest.raises(ValidationError):
        Settings()


def test_secret_never_in_repr(env: pytest.MonkeyPatch) -> None:
    settings = Settings()
    assert "s3cr3t-value" not in repr(settings)
    assert "s3cr3t-value" not in str(settings)


def test_load_settings_returns_settings(env: pytest.MonkeyPatch) -> None:
    assert load_settings().app_env == "production"


def test_load_settings_exits_and_names_missing_variable(
    env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    env.delenv("REDIS_URL")
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as exc_info:
        load_settings()

    assert exc_info.value.code != 0
    assert "REDIS_URL" in caplog.text


def test_load_settings_never_logs_secret_value(
    env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Invalid CORS makes validation fail while DATABASE_URL holds a secret.
    env.setenv("CORS_ALLOWED_ORIGINS", "http://evil.example")
    with caplog.at_level(logging.DEBUG), pytest.raises(SystemExit) as exc_info:
        load_settings()

    assert "CORS_ALLOWED_ORIGINS" in caplog.text
    assert "s3cr3t-value" not in caplog.text
    assert "http://evil.example" not in caplog.text
    assert "s3cr3t-value" not in str(exc_info.value)


def test_get_settings_is_cached(env: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
