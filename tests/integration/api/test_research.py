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
from api.main import create_app
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


def _client(service: RunService, user_id: uuid.UUID | None = None) -> httpx.AsyncClient:
    app = create_app(SETTINGS, run_service=service)
    cookies = mint_test_session(user_id, SETTINGS) if user_id else None
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


async def test_other_user_gets_404(engine: Engine, service: RunService) -> None:
    owner, other = add_user(engine, "a@example.test"), add_user(engine, "b@example.test")
    async with _client(service, owner) as client:
        run_id = await _start(client, service)
    async with _client(service, other) as client:
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
