"""Tests for the Phase 3 settings in api.config (T004)."""

import logging

import pytest
from pydantic import ValidationError

from api.config import Settings, load_settings

STRONG_SECRET = "jwt-secret-value-that-is-long-enough-123"  # noqa: S105
PROVIDER_KEY = "provider-key-value-xyz"

PHASE3_VARS = (
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "GITHUB_MODELS_TOKEN",
    "OPENROUTER_API_KEY",
    "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_AI_TOKEN",
    "LLM_PROVIDERS_FILE",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GITHUB_CLIENT_ID",
    "GITHUB_CLIENT_SECRET",
    "JWT_SECRET",
    "SESSION_COOKIE_DOMAIN",
    "WEB_ORIGIN",
    "TURNSTILE_SECRET_KEY",
    "QUOTA_DAILY_QUESTIONS",
    "RATE_LIMIT_USER_PER_MIN",
    "RATE_LIMIT_IP_PER_MIN",
    "AGENT_MAX_MODEL_CALLS",
    "AGENT_DEADLINE_SECONDS",
    "AGENT_MAX_TOOL_CALLS",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in PHASE3_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@postgres:5432/d")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://disclosly.com")
    monkeypatch.setenv("WEB_ORIGIN", "https://disclosly.com")
    monkeypatch.setenv("JWT_SECRET", STRONG_SECRET)
    return monkeypatch


def test_defaults(env: pytest.MonkeyPatch) -> None:
    settings = Settings()

    assert settings.gemini_api_key is None
    assert settings.llm_providers_file == "llm/providers.yaml"
    assert settings.session_cookie_domain == ".disclosly.com"
    assert settings.quota_daily_questions == 20
    assert settings.rate_limit_user_per_min == 30
    assert settings.rate_limit_ip_per_min == 60
    assert settings.agent_max_model_calls == 4
    assert settings.agent_deadline_seconds == 90
    assert settings.agent_max_tool_calls == 6
    assert settings.otel_exporter_otlp_endpoint is None


@pytest.mark.parametrize(
    "name",
    [
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "GITHUB_MODELS_TOKEN",
        "OPENROUTER_API_KEY",
        "CLOUDFLARE_AI_TOKEN",
        "GOOGLE_CLIENT_SECRET",
        "GITHUB_CLIENT_SECRET",
        "TURNSTILE_SECRET_KEY",
    ],
)
def test_secrets_are_secretstr_and_hidden(env: pytest.MonkeyPatch, name: str) -> None:
    env.setenv(name, PROVIDER_KEY)
    settings = Settings()

    value = getattr(settings, name.lower())
    assert value.get_secret_value() == PROVIDER_KEY
    assert PROVIDER_KEY not in repr(settings)
    assert STRONG_SECRET not in repr(settings)


def test_empty_provider_key_is_unset(env: pytest.MonkeyPatch) -> None:
    env.setenv("GEMINI_API_KEY", "")
    env.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    settings = Settings()
    assert settings.gemini_api_key is None
    assert settings.otel_exporter_otlp_endpoint is None


def test_jwt_secret_required_in_production(env: pytest.MonkeyPatch) -> None:
    env.delenv("JWT_SECRET")
    with pytest.raises(ValidationError):
        Settings()


def test_jwt_secret_too_short_in_production(env: pytest.MonkeyPatch) -> None:
    env.setenv("JWT_SECRET", "CHANGE_ME")
    with pytest.raises(ValidationError) as excinfo:
        Settings()
    messages = [error["msg"] for error in excinfo.value.errors(include_input=False)]
    assert messages
    assert all("CHANGE_ME" not in message for message in messages)


@pytest.mark.parametrize("app_env", ["development", "test"])
def test_jwt_secret_optional_in_development_and_test(env: pytest.MonkeyPatch, app_env: str) -> None:
    env.setenv("APP_ENV", app_env)
    env.setenv("WEB_ORIGIN", "https://disclosly.com")
    env.delenv("JWT_SECRET")
    assert Settings().jwt_secret is None
    env.setenv("JWT_SECRET", "short")
    assert Settings().jwt_secret is not None


def test_web_origin_localhost_only_in_development(env: pytest.MonkeyPatch) -> None:
    env.setenv("WEB_ORIGIN", "http://localhost:3000")
    with pytest.raises(ValidationError):
        Settings()
    env.setenv("APP_ENV", "development")
    assert Settings().web_origin == "http://localhost:3000"


def test_web_origin_strips_trailing_slash(env: pytest.MonkeyPatch) -> None:
    env.setenv("WEB_ORIGIN", "https://disclosly.com/")
    assert Settings().web_origin == "https://disclosly.com"


@pytest.mark.parametrize(
    "name",
    [
        "QUOTA_DAILY_QUESTIONS",
        "RATE_LIMIT_USER_PER_MIN",
        "RATE_LIMIT_IP_PER_MIN",
        "AGENT_MAX_MODEL_CALLS",
        "AGENT_DEADLINE_SECONDS",
    ],
)
def test_limits_must_be_positive(env: pytest.MonkeyPatch, name: str) -> None:
    env.setenv(name, "0")
    with pytest.raises(ValidationError):
        Settings()


def test_load_settings_error_names_variable_not_value(
    env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    env.setenv("JWT_SECRET", "too-short-secret-value")
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit):
        load_settings()
    assert "JWT_SECRET" in caplog.text
    assert "too-short-secret-value" not in caplog.text
