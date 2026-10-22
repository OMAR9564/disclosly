"""Integration tests for /v1/me (T036) and the allowlist CLI (T033)."""

import uuid
from typing import cast

import httpx
import pytest
from sqlalchemy import Engine, select

from agent.loop import Budget
from agent.runs import RunService, StreamRedis
from api import schema
from api.auth import allowlist
from api.auth.session import mint_test_session
from api.config import Settings
from api.main import create_app
from tests.agent_fakes import FakeTools, ScriptedRouter
from tests.api_fakes import FakeRedis, fake_guards
from tests.integration.agent.helpers import add_user

SETTINGS = Settings(
    app_env="test",
    database_url="postgresql://u:p@localhost:5432/db",
    redis_url="redis://localhost:6379/0",
    cors_allowed_origins=["https://disclosly.com"],
    jwt_secret="jwt-secret-value-that-is-long-enough-123",  # noqa: S106
    quota_daily_questions=5,
)


def _client(
    engine: Engine, user: uuid.UUID, user_per_min: int = 30, ip_per_min: int = 60
) -> httpx.AsyncClient:
    redis = FakeRedis()
    guards = fake_guards(
        redis=redis,
        engine=engine,
        quota_limit=5,
        deep_limit=2,
        user_per_min=user_per_min,
        ip_per_min=ip_per_min,
    )
    service = RunService(
        engine, cast(StreamRedis, redis), ScriptedRouter([]), FakeTools({}), Budget()
    )
    app = create_app(SETTINGS, run_service=service, guards=guards)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        cookies=mint_test_session(user, SETTINGS),
    )


async def test_get_me(engine: Engine) -> None:
    async with _client(engine, add_user(engine)) as client:
        response = await client.get("/v1/me")
    assert response.status_code == 200
    body = response.json()
    assert body["display_name"] == "Test User"
    assert body["email_masked"] == "u***@example.test"
    assert body["language"] == "tr"
    simple, deep = body["quota"]["simple"], body["quota"]["deep"]
    assert {k: simple[k] for k in ("limit", "used", "remaining")} == {
        "limit": 5,
        "used": 0,
        "remaining": 5,
    }
    assert {k: deep[k] for k in ("limit", "used", "remaining")} == {
        "limit": 2,
        "used": 0,
        "remaining": 2,
    }
    assert simple["resets_at"].endswith("Z") or "+00:00" in simple["resets_at"]
    assert deep["resets_at"] == simple["resets_at"]


async def test_put_language(engine: Engine) -> None:
    user = add_user(engine)
    async with _client(engine, user) as client:
        response = await client.put("/v1/me", json={"language": "en"})
        assert response.status_code == 200
        assert response.json()["language"] == "en"
        bad = await client.put("/v1/me", json={"language": "de"})
    assert bad.status_code == 422
    with engine.connect() as conn:
        language = conn.execute(
            select(schema.users.c.language).where(schema.users.c.id == user)
        ).scalar()
    assert language == "en"


async def test_me_requires_session(engine: Engine) -> None:
    async with _client(engine, add_user(engine)) as client:
        client.cookies.clear()
        response = await client.get("/v1/me")
    assert response.status_code == 401


async def test_user_rate_limit(engine: Engine) -> None:
    async with _client(engine, add_user(engine), user_per_min=2) as client:
        assert (await client.get("/v1/me")).status_code == 200
        assert (await client.get("/v1/me")).status_code == 200
        response = await client.get("/v1/me")
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert int(response.headers["retry-after"]) >= 1


async def test_ip_rate_limit(engine: Engine) -> None:
    async with _client(engine, add_user(engine), ip_per_min=1) as client:
        assert (await client.get("/v1/me")).status_code == 200
        response = await client.get("/v1/me")
    assert response.status_code == 429


def test_allowlist_cli(engine: Engine, capsys: pytest.CaptureFixture[str]) -> None:
    assert allowlist.main(["add", " Ada@Example.com ", "--note", "friend"], engine) == 0
    assert allowlist.main(["add", "ada@example.com"], engine) == 0  # idempotent
    assert allowlist.main(["add", "nope"], engine) == 2
    assert allowlist.db_lookup(engine)(allowlist.email_hash("ada@example.com"))
    assert allowlist.main(["list"], engine) == 0
    assert "a***@example.com" in capsys.readouterr().out
    assert allowlist.main(["remove", "ADA@example.com"], engine) == 0
    assert allowlist.main(["remove", "ada@example.com"], engine) == 1
    assert not allowlist.db_lookup(engine)(allowlist.email_hash("ada@example.com"))
