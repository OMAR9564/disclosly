"""Environment configuration (contract: specs/001-walking-skeleton/contracts/configuration.md)."""

import logging
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = logging.getLogger(__name__)

AppEnv = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


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
