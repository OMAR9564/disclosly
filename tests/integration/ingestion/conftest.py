"""Migrated corpus database for ingestion integration tests."""

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

from api.config import get_settings
from ingestion.db import make_engine

LOCAL_DATABASE_URL = "postgresql://disclosly:CHANGE_ME@localhost:5432/disclosly"


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    url = os.environ.get("DATABASE_URL", LOCAL_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("REDIS_URL", os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://disclosly.com")
    get_settings.cache_clear()
    config = Config("alembic.ini")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    db = make_engine(url)
    yield db
    db.dispose()
    command.downgrade(config, "base")
    get_settings.cache_clear()
