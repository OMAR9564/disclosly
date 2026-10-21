"""Integration tests for agent.runs (T018): Postgres rows and Redis event streams."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import Engine, insert, select

from agent.loop import Budget
from agent.runs import STREAM_TTL_SECONDS, RunService, mark_interrupted, stream_key
from api import schema
from llm.types import ChatRequest, ChatResponse, TaskClass
from tests.integration.agent.helpers import (
    FACT,
    FakeTools,
    add_user,
    good_script,
    redis_client,
)
from tools.types import SeenItem

QUESTION = "THYAO 2025 üçüncü çeyrek net kârı ne kadar?"


@pytest.fixture
async def redis() -> AsyncIterator[Any]:
    async for client in redis_client():
        yield client


def _service(
    engine: Engine, redis: Any, router: Any, items: list[SeenItem] | None = None
) -> RunService:
    tools = FakeTools({"get_financial_facts": items if items is not None else [FACT]})
    return RunService(engine, redis, router, tools, Budget())


async def _events(service: RunService, run_id: uuid.UUID) -> list[tuple[str, dict[str, Any]]]:
    return [(e.event, e.data) async for e in service.events(run_id, block_ms=100) if e is not None]


async def test_run_streams_and_persists(engine: Engine, redis: Any) -> None:
    user = add_user(engine)
    service = _service(engine, redis, good_script())
    run_id = await service.create(user, QUESTION)
    await service.wait_idle()

    events = await _events(service, run_id)
    assert [e for e, _ in events] == [
        "run_started",
        "model_called",
        "tool_called",
        "tool_result",
        "model_called",
        "final",
    ]
    assert events[0][1] == {"run_id": str(run_id), "language": "tr"}
    final = events[-1][1]
    assert final["status"] == "completed"
    assert final["citations"][0]["cite_id"] == "f:1"
    assert 0 < await redis.ttl(stream_key(run_id)) <= STREAM_TTL_SECONDS

    run = await service.get(run_id, user)
    assert run is not None
    assert run["status"] == "completed"
    assert run["answer"]["disclaimer"] == "Yatırım tavsiyesi değildir / Not investment advice"
    assert run["usage"]["model_calls"] == 2
    assert run["usage"]["time_to_first_event_ms"] is not None
    assert run["usage"]["total_ms"] >= run["usage"]["time_to_first_event_ms"]
    assert run["citations"] == [{"cite_id": "f:1", "excerpt": FACT.text, "source": FACT.source}]


async def test_resume_after_last_event_id(engine: Engine, redis: Any) -> None:
    user = add_user(engine)
    service = _service(engine, redis, good_script())
    run_id = await service.create(user, QUESTION)
    await service.wait_idle()
    all_events = [e async for e in service.events(run_id, block_ms=100) if e is not None]
    rest = [e async for e in service.events(run_id, all_events[2].id, block_ms=100) if e]
    assert [e.event for e in rest] == [e.event for e in all_events[3:]]


async def test_other_user_cannot_read(engine: Engine, redis: Any) -> None:
    owner, other = add_user(engine, "a@example.test"), add_user(engine, "b@example.test")
    service = _service(engine, redis, good_script())
    run_id = await service.create(owner, QUESTION)
    await service.wait_idle()
    assert await service.get(run_id, other) is None
    assert await service.get(uuid.uuid4(), owner) is None


async def test_long_excerpt_is_truncated(engine: Engine, redis: Any) -> None:
    from decimal import Decimal

    long_fact = SeenItem(
        cite_id="f:1",
        text="net_income 2025-Q3: 1234.5 million TRY " + "x" * 3000,
        value=Decimal("1234.5"),
        source={},
    )
    user = add_user(engine)
    service = _service(engine, redis, good_script(), [long_fact])
    run_id = await service.create(user, QUESTION)
    await service.wait_idle()
    run = await service.get(run_id, user)
    assert run is not None
    assert len(run["citations"][0]["excerpt"]) == 1500


class BrokenRouter:
    async def chat(self, request: ChatRequest, *, task_class: TaskClass) -> ChatResponse:
        raise RuntimeError("boom")


async def test_failure_is_recorded_and_streamed(engine: Engine, redis: Any) -> None:
    user = add_user(engine)
    service = _service(engine, redis, BrokenRouter())
    run_id = await service.create(user, QUESTION)
    await service.wait_idle()
    events = await _events(service, run_id)
    assert events[-1] == ("error", {"code": "internal_error", "message": "Sunucu hatası."})
    run = await service.get(run_id, user)
    assert run is not None
    assert (run["status"], run["error_code"]) == ("failed", "internal_error")


def test_mark_interrupted(engine: Engine) -> None:
    user = add_user(engine)
    with engine.begin() as conn:
        conn.execute(insert(schema.runs).values(user_id=user, question=QUESTION, language="tr"))
    assert mark_interrupted(engine) == 1
    with engine.connect() as conn:
        row = conn.execute(select(schema.runs.c.status, schema.runs.c.error_code)).one()
    assert tuple(row) == ("failed", "interrupted")
