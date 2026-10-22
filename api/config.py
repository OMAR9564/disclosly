"""Environment configuration (contract: specs/001-walking-skeleton/contracts/configuration.md)."""

import ipaddress
import logging
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = logging.getLogger(__name__)

AppEnv = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

JWT_SECRET_MIN_LENGTH = 32


class Settings(BaseSettings):
    """API settings, read from environment variables only."""

    model_config = SettingsConfigDict(case_sensitive=False, frozen=True)

    app_env: AppEnv
    app_version: str = "dev"
    database_url: SecretStr
    redis_url: str
    cors_allowed_origins: Annotated[list[str], NoDecode, Field(min_length=1)]
    log_level: LogLevel = "INFO"
    prometheus_multiproc_dir: str | None = None

    # Model providers (specs/003-single-agent-mvp/contracts/model-router.md).
    # An unset key means the provider is skipped.
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    cloudflare_account_id: str | None = None
    cloudflare_ai_token: SecretStr | None = None
    llm_providers_file: str = "llm/providers.yaml"

    # Auth (research §R4).
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    github_client_id: str | None = None
    github_client_secret: SecretStr | None = None
    jwt_secret: SecretStr | None = None
    session_cookie_domain: str = ".disclosly.com"
    web_origin: str = "https://disclosly.com"
    # Public origin of the API itself; OAuth redirect URIs are built from it.
    api_origin: str = "https://api.disclosly.com"
    turnstile_secret_key: SecretStr | None = None
    # Peers whose `CF-Connecting-IP` header is trusted (Caddy's network), as CIDRs.
    trusted_proxies: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Quotas, rate limits and agent budget.
    quota_daily_questions: int = Field(default=20, ge=1)
    rate_limit_user_per_min: int = Field(default=30, ge=1)
    rate_limit_ip_per_min: int = Field(default=60, ge=1)
    agent_max_model_calls: int = Field(default=4, ge=1)
    agent_deadline_seconds: int = Field(default=90, ge=1)
    agent_max_tool_calls: int = Field(default=6, ge=0)

    otel_exporter_otlp_endpoint: str | None = None

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]
        return value

    @field_validator("cors_allowed_origins")
    @classmethod
    def _check_origins(cls, value: list[str], info: ValidationInfo) -> list[str]:
        app_env = info.data.get("app_env")
        for origin in value:
            if not _is_allowed_origin(origin, app_env):
                # The origin itself is not echoed: error messages stay free of values.
                raise ValueError(
                    "entries must be absolute https:// origins "
                    "(http://localhost is allowed only when APP_ENV=development)"
                )
        return value

    @field_validator(
        "gemini_api_key",
        "groq_api_key",
        "openrouter_api_key",
        "cloudflare_account_id",
        "cloudflare_ai_token",
        "google_client_id",
        "google_client_secret",
        "github_client_id",
        "github_client_secret",
        "jwt_secret",
        "turnstile_secret_key",
        "otel_exporter_otlp_endpoint",
        mode="before",
    )
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        # `.env.example` leaves optional values empty; treat that as "not set".
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("jwt_secret")
    @classmethod
    def _check_jwt_secret(cls, value: SecretStr | None, info: ValidationInfo) -> SecretStr | None:
        if info.data.get("app_env") in ("development", "test"):
            return value
        if value is None:
            raise ValueError("required outside development and test")
        if len(value.get_secret_value()) < JWT_SECRET_MIN_LENGTH:
            raise ValueError(f"must be at least {JWT_SECRET_MIN_LENGTH} characters")
        return value

    @field_validator("trusted_proxies", mode="before")
    @classmethod
    def _split_proxies(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("trusted_proxies")
    @classmethod
    def _check_proxies(cls, value: list[str]) -> list[str]:
        for network in value:
            try:
                ipaddress.ip_network(network, strict=False)
            except ValueError:
                raise ValueError("entries must be IP addresses or CIDR networks") from None
        return value

    @field_validator("web_origin", "api_origin", mode="before")
    @classmethod
    def _strip_web_origin(cls, value: object) -> object:
        return value.strip().rstrip("/") if isinstance(value, str) else value

    @field_validator("web_origin", "api_origin")
    @classmethod
    def _check_web_origin(cls, value: str, info: ValidationInfo) -> str:
        if not _is_allowed_origin(value, info.data.get("app_env")):
            raise ValueError(
                "must be an absolute https:// origin "
                "(http://localhost is allowed only when APP_ENV=development)"
            )
        return value


def _is_allowed_origin(origin: str, app_env: object) -> bool:
    parts = urlsplit(origin)
    if not parts.hostname or parts.path or parts.query or parts.fragment:
        return False
    if parts.username is not None or parts.password is not None:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and parts.hostname == "localhost" and app_env == "development"


def load_settings() -> Settings:
    """Build Settings or exit non-zero, logging variable names but never values."""
    try:
        return Settings()
    except ValidationError as exc:
        problems = []
        for error in exc.errors(include_input=False, include_url=False):
            name = str(error["loc"][0]).upper() if error["loc"] else "SETTINGS"
            problems.append(f"{name} ({error['type']})")
        logger.error("invalid configuration: %s", ", ".join(problems))
        raise SystemExit(1) from None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, loaded once."""
    return load_settings()


if __name__ == "__main__":  # pragma: no cover - exercised by api/entrypoint.sh in the image
    # Fail fast before uvicorn starts: its supervisor would keep respawning failing workers.
    from api.logging import configure_logging

    configure_logging("INFO")
    load_settings()
