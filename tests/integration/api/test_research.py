"""Integration tests for the research endpoints (T019)."""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine

from agent.loop import Budget
from agent.runs import RunService, stream_key
from api.auth.session import mint_test_session
from api.config import Settings
from api.guards import Guards
from api.main import create_app
from tests.api_fakes import fake_guards
from tests.integration.agent.helpers import FACT, FakeTools, add_user, good_script, redis_client

QUESTION = "THYAO 2025 üçüncü çeyrek net kârı ne kadar?"
SETTINGS = Settings(
    app_env="test",
    database_url="postgresql://u:p@localhost:5432/db",
    redis_url="redis://localhost:6379/0",
    cors_allowed_origins=["https://disclosly.com"],
    jwt_secret="jwt-secret-value-that-is-long-enough-123",  # noqa: S106
)


@pytest.fixture
async def redis() -> AsyncIterator[Any]:
    async for client in redis_client():
        yield client


@pytest.fixture
def service(engine: Engine, redis: Any) -> RunService:
    tools = FakeTools({"get_financial_facts": [FACT]})
    return RunService(engine, redis, good_script(), tools, Budget())


ALLOWED = {"user@example.test", "a@example.test", "b@example.test"}


def _client(
    service: RunService,
    user_id: uuid.UUID | None = None,
    email: str = "user@example.test",
    guards: Guards | None = None,
) -> httpx.AsyncClient:
    app = create_app(SETTINGS, run_service=service, guards=guards or fake_guards(ALLOWED))
    cookies = mint_test_session(user_id, SETTINGS, email) if user_id else None
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", cookies=cookies
    )


def parse_sse(body: str) -> list[dict[str, Any]]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in fields:
            events.append(
                {"id": fields["id"], "event": fields["event"], "data": json.loads(fields["data"])}
            )
    return events


async def _start(client: httpx.AsyncClient, service: RunService) -> str:
    response = await client.post("/v1/research", json={"question": QUESTION})
    assert response.status_code == 202
    await service.wait_idle()
    run_id = response.json()["run_id"]
    assert isinstance(run_id, str)
    return run_id


async def test_full_stream_and_run(engine: Engine, service: RunService) -> None:
    user = add_user(engine)
    async with _client(service, user) as client:
        run_id = await _start(client, service)
        stream = await client.get(f"/v1/research/{run_id}/stream")
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(stream.text)
        assert [e["event"] for e in events] == [
            "run_started",
            "model_called",
            "tool_called",
            "tool_result",
            "model_called",
            "final",
        ]
        final = events[-1]["data"]
        assert final["status"] == "completed"
        assert final["answer"]["disclaimer"].startswith("Yatırım tavsiyesi değildir")
        assert final["citations"][0]["excerpt"] == FACT.text

        run = (await client.get(f"/v1/research/{run_id}")).json()
        assert run["status"] == "completed"
        assert run["question"] == QUESTION
        assert run["citations"][0]["cite_id"] == "f:1"


async def test_reload_replays_after_last_event_id(engine: Engine, service: RunService) -> None:
    user = add_user(engine)
    async with _client(service, user) as client:
        run_id = await _start(client, service)
        events = parse_sse((await client.get(f"/v1/research/{run_id}/stream")).text)
        resumed = await client.get(
            f"/v1/research/{run_id}/stream", headers={"Last-Event-ID": events[1]["id"]}
        )
        assert [e["event"] for e in parse_sse(resumed.text)] == [e["event"] for e in events[2:]]


async def test_expired_stream_replays_stored_result(
    engine: Engine, service: RunService, redis: Any
) -> None:
    user = add_user(engine)
    async with _client(service, user) as client:
        run_id = await _start(client, service)
        await redis.delete(stream_key(uuid.UUID(run_id)))
        events = parse_sse((await client.get(f"/v1/research/{run_id}/stream")).text)
        assert [e["event"] for e in events] == ["final"]
        assert events[0]["data"]["status"] == "completed"


async def test_cancelled_stream_read_ends_without_an_error(
    engine: Engine, service: RunService, monkeypatch: pytest.MonkeyPatch
) -> None:
    from redis.exceptions import TimeoutError as RedisTimeoutError

    user = add_user(engine)
    async with _client(service, user) as client:
        run_id = await _start(client, service)

        async def cancelled(*args: Any, **kwargs: Any) -> AsyncIterator[None]:
            raise RedisTimeoutError("Timeout reading from redis:6379")
            yield None

        monkeypatch.setattr(service, "events", cancelled)
        response = await client.get(f"/v1/research/{run_id}/stream")
    assert response.status_code == 200
    assert response.text == ""


async def test_other_user_gets_404(engine: Engine, service: RunService) -> None:
    owner, other = add_user(engine, "a@example.test"), add_user(engine, "b@example.test")
    async with _client(service, owner, "a@example.test") as client:
        run_id = await _start(client, service)
    async with _client(service, other, "b@example.test") as client:
        for path in (f"/v1/research/{run_id}", f"/v1/research/{run_id}/stream"):
            response = await client.get(path)
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "not_found"


async def test_requires_session(service: RunService) -> None:
    async with _client(service) as client:
        response = await client.post("/v1/research", json={"question": QUESTION})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize("question", ["ab", "   ab   ", "x" * 1001])
async def test_question_length(engine: Engine, service: RunService, question: str) -> None:
    async with _client(service, add_user(engine)) as client:
        response = await client.post("/v1/research", json={"question": question})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


async def test_cors_allows_web_origin_with_credentials(service: RunService) -> None:
    async with _client(service) as client:
        response = await client.options(
            "/v1/research",
            headers={"Origin": "https://disclosly.com", "Access-Control-Request-Method": "POST"},
        )
        assert response.headers["access-control-allow-origin"] == "https://disclosly.com"
        assert response.headers["access-control-allow-credentials"] == "true"


class UnavailableRouter:
    async def chat(self, request: Any, *, task_class: Any) -> Any:
        from llm.types import ProvidersUnavailable

        raise ProvidersUnavailable(task_class, ["gemini-flash-lite", "groq-llama"])


async def test_providers_unavailable_is_a_localized_error_event(engine: Engine, redis: Any) -> None:
    from sqlalchemy import select

    from api import schema

    service = RunService(engine, redis, UnavailableRouter(), FakeTools({}), Budget())
    async with _client(service, add_user(engine)) as client:
        run_id = await _start(client, service)
        events = parse_sse((await client.get(f"/v1/research/{run_id}/stream")).text)
    assert events[-1]["event"] == "error"
    assert events[-1]["data"] == {
        "code": "providers_unavailable",
        "message": "Yanıt verme geçici olarak kullanılamıyor. Lütfen sonra deneyin.",
    }
    with engine.begin() as conn:
        row = conn.execute(
            select(schema.runs.c.status, schema.runs.c.error_code).where(
                schema.runs.c.id == uuid.UUID(run_id)
            )
        ).one()
    assert (row.status, row.error_code) == ("failed", "providers_unavailable")


async def test_start_returns_quota_and_refunds_early_failure(engine: Engine, redis: Any) -> None:
    guards = fake_guards(ALLOWED, quota_limit=2)
    user = add_user(engine)
    service = RunService(engine, redis, UnavailableRouter(), FakeTools({}), Budget())
    async with _client(service, user, guards=guards) as client:
        response = await client.post("/v1/research", json={"question": QUESTION})
        assert response.status_code == 202
        assert response.json()["quota"]["simple"]["remaining"] == 1
        assert response.json()["quota"]["deep"]["remaining"] == 5
        await service.wait_idle()
    # The run failed before any model answered, so the question was given back.
    assert (await guards.quota.status(user)).used == 0


async def test_quota_exhausted_has_reset_time(engine: Engine, service: RunService) -> None:
    guards = fake_guards(ALLOWED, quota_limit=1)
    async with _client(service, add_user(engine), guards=guards) as client:
        await _start(client, service)
        response = await client.post(
            "/v1/research", json={"question": QUESTION}, headers={"Accept-Language": "en"}
        )
    assert response.status_code == 429
    body = response.json()["error"]
    assert body["code"] == "quota_exhausted"
    assert "00:00 (TRT)" in body["message"]
