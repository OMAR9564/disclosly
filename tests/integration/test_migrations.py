"""Alembic migrations run against a real Postgres (CI service container or `make up`)."""

import os
from collections.abc import Iterator

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from api.config import get_settings

pytestmark = pytest.mark.integration

LOCAL_DATABASE_URL = "postgresql://disclosly:CHANGE_ME@localhost:5432/disclosly"


@pytest.fixture
def database_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    url = os.environ.get("DATABASE_URL", LOCAL_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("REDIS_URL", os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://disclosly.com")
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


def _current_revision(url: str) -> str | None:
    with psycopg.connect(url) as conn:
        exists = conn.execute("SELECT to_regclass('public.alembic_version')").fetchone()
        if exists is None or exists[0] is None:
            return None
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return None if row is None else str(row[0])


def test_upgrade_head_then_downgrade_base(database_url: str) -> None:
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    assert _current_revision(database_url) == "0001"

    command.downgrade(config, "base")
    assert _current_revision(database_url) is None
