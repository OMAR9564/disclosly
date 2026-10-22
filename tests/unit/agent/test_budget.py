"""agent.budget (T007): limits, absolute deadlines, per-node usage."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from agent.budget import (
    LimitReached,
    ModeBudget,
    Usage,
    guarded_call,
    remaining_seconds,
    take_sandbox_run,
    take_tool_call,
)
from api.config import Settings
from llm.types import ChatMessage, ChatRequest, ChatResponse, TaskClass

T0 = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
REQUEST = ChatRequest(messages=[ChatMessage(role="user", content="hi")])


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class CountingRouter:
    def __init__(self, delay: float = 0.0) -> None:
        self.calls: list[TaskClass] = []
        self.delay = delay

    async def chat(self, request: ChatRequest, *, task_class: TaskClass) -> ChatResponse:
        self.calls.append(task_class)
        if self.delay:
            await asyncio.sleep(self.delay)
        return ChatResponse(
            content="ok", provider="groq", model="m", tokens_in=3, tokens_out=2, cache_hit=True
        )


def test_default_budgets_match_the_contract() -> None:
    simple, deep = ModeBudget.simple(), ModeBudget.deep()
    assert (simple.max_model_calls, simple.deadline_seconds, simple.max_tool_calls) == (4, 90, 6)
    assert simple.max_steps is None
    assert (deep.max_model_calls, deep.deadline_seconds, deep.max_steps) == (20, 300, 12)
    assert (deep.max_tool_calls, deep.max_sandbox_runs) == (24, 4)


def test_budgets_from_settings() -> None:
    settings = Settings(
        app_env="test",
        database_url="postgresql://u:p@localhost:5432/db",
        redis_url="redis://localhost:6379/0",
        cors_allowed_origins=["https://disclosly.com"],
        agent_max_model_calls=5,
        agent_deep_max_model_calls=15,
        agent_deep_max_steps=9,
        agent_deep_max_sandbox_runs=2,
    )
    assert ModeBudget.simple(settings).max_model_calls == 5
    deep = ModeBudget.deep(settings)
    assert (deep.max_model_calls, deep.max_steps, deep.max_sandbox_runs) == (15, 9, 2)


async def test_limit_at_exactly_n_calls() -> None:
    router, usage, budget = CountingRouter(), Usage(), ModeBudget.simple()
    deadline = T0 + timedelta(seconds=90)
    for _ in range(4):
        await guarded_call(router, REQUEST, "cheap", usage, budget, deadline, Clock(), node="r")
    with pytest.raises(LimitReached) as excinfo:
        await guarded_call(router, REQUEST, "cheap", usage, budget, deadline, Clock(), node="r")

    assert excinfo.value.limit == "model_calls"
    assert len(router.calls) == 4
    assert usage.model_calls == 4
    assert usage.nodes["r"].model_calls == 4
    assert usage.tokens_in == 12
    assert usage.tokens_out == 8
    assert usage.providers == ["groq"] * 4
    assert usage.calls_left(budget) == 0


async def test_deadline_in_the_past_makes_no_call() -> None:
    router, usage = CountingRouter(), Usage()
    with pytest.raises(LimitReached) as excinfo:
        await guarded_call(
            router, REQUEST, "strong", usage, ModeBudget.deep(), T0 - timedelta(seconds=1), Clock()
        )
    assert excinfo.value.limit == "deadline"
    assert router.calls == []
    assert usage.model_calls == 0


async def test_slow_call_is_cut_at_the_deadline() -> None:
    clock = Clock()
    with pytest.raises(LimitReached) as excinfo:
        await guarded_call(
            CountingRouter(delay=1.0),
            REQUEST,
            "cheap",
            Usage(),
            ModeBudget.simple(),
            T0 + timedelta(seconds=0.05),
            clock,
        )
    assert excinfo.value.limit == "deadline"


async def test_deadline_restored_after_a_simulated_restart() -> None:
    # The absolute deadline round-trips through a checkpoint; the new process's clock is later.
    usage = Usage()
    budget = ModeBudget.deep()
    deadline = T0 + timedelta(seconds=300)
    stored: dict[str, Any] = {"deadline_at": deadline.isoformat(), "usage": usage.model_dump()}

    restored_usage = Usage.model_validate(stored["usage"])
    restored_deadline = datetime.fromisoformat(stored["deadline_at"])
    later = Clock(T0 + timedelta(seconds=299))
    assert remaining_seconds(restored_deadline, later) == pytest.approx(1.0)
    await guarded_call(
        CountingRouter(), REQUEST, "cheap", restored_usage, budget, restored_deadline, later
    )

    after = Clock(T0 + timedelta(seconds=301))
    with pytest.raises(LimitReached):
        await guarded_call(
            CountingRouter(), REQUEST, "cheap", restored_usage, budget, restored_deadline, after
        )


async def test_no_deadline_before_approval() -> None:
    assert remaining_seconds(None) is None
    usage = Usage()
    await guarded_call(CountingRouter(), REQUEST, "strong", usage, ModeBudget.deep(), None)
    assert usage.model_calls == 1


async def test_model_called_event() -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event: str, data: dict[str, Any]) -> None:
        events.append((event, data))

    await guarded_call(
        CountingRouter(), REQUEST, "cheap", Usage(), ModeBudget.simple(), None, emit=emit
    )
    assert events == [
        (
            "model_called",
            {"task_class": "cheap", "provider": "groq", "cache_hit": True, "fallback_from": None},
        )
    ]


def test_tool_and_sandbox_limits() -> None:
    usage, budget = Usage(), ModeBudget.deep()
    for _ in range(24):
        take_tool_call(usage, budget, "retriever")
    with pytest.raises(LimitReached) as tools:
        take_tool_call(usage, budget, "retriever")
    assert tools.value.limit == "tool_calls"
    assert usage.nodes["retriever"].tool_calls == 24

    for _ in range(4):
        take_sandbox_run(usage, budget)
    with pytest.raises(LimitReached) as runs:
        take_sandbox_run(usage, budget)
    assert runs.value.limit == "sandbox_runs"
    with pytest.raises(LimitReached):
        take_sandbox_run(Usage(), ModeBudget.simple())


def test_usage_round_trips() -> None:
    usage = Usage(model_calls=2)
    usage.node("planner").ms = 12.5
    assert Usage.model_validate(usage.model_dump()) == usage
