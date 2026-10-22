"""Per-mode budgets and the one guard every graph model call goes through (research §R3).

Limits live in code, never with the model. Usage is part of the graph state, so it survives a
restart; the deadline is an absolute UTC timestamp for the same reason. Simple runs get their
deadline when the router starts them, deep runs when the plan is approved (planning counts
toward the call limit but not the clock).
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from llm.router import Router
from llm.types import ChatRequest, ChatResponse, TaskClass

if TYPE_CHECKING:
    from api.config import Settings

Mode = Literal["simple", "deep"]
LimitHit = Literal["model_calls", "deadline", "steps", "tool_calls", "sandbox_runs"]
Clock = Callable[[], datetime]
Emit = Callable[[str, dict[str, Any]], Awaitable[None]]


def utcnow() -> datetime:
    return datetime.now(UTC)


class ModeBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Mode
    max_model_calls: int = Field(ge=1)
    deadline_seconds: int = Field(ge=1)
    max_tool_calls: int = Field(ge=0)
    # Graph steps after approval and sandbox executions: deep mode only.
    max_steps: int | None = Field(default=None, ge=1)
    max_sandbox_runs: int = Field(default=0, ge=0)

    @classmethod
    def simple(cls, settings: "Settings | None" = None) -> "ModeBudget":
        if settings is None:
            return cls(mode="simple", max_model_calls=4, deadline_seconds=90, max_tool_calls=6)
        return cls(
            mode="simple",
            max_model_calls=settings.agent_max_model_calls,
            deadline_seconds=settings.agent_deadline_seconds,
            max_tool_calls=settings.agent_max_tool_calls,
        )

    @classmethod
    def deep(cls, settings: "Settings | None" = None) -> "ModeBudget":
        if settings is None:
            return cls(
                mode="deep",
                max_model_calls=20,
                deadline_seconds=300,
                max_tool_calls=24,
                max_steps=12,
                max_sandbox_runs=4,
            )
        return cls(
            mode="deep",
            max_model_calls=settings.agent_deep_max_model_calls,
            deadline_seconds=settings.agent_deep_deadline_seconds,
            max_tool_calls=settings.agent_deep_max_tool_calls,
            max_steps=settings.agent_deep_max_steps,
            max_sandbox_runs=settings.agent_deep_max_sandbox_runs,
        )


class NodeUsage(BaseModel):
    model_calls: int = 0
    tool_calls: int = 0
    ms: float = 0.0


class Usage(BaseModel):
    """What a run has spent; stored in `runs.usage` with the per-node breakdown."""

    model_calls: int = 0
    tool_calls: int = 0
    sandbox_runs: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    providers: list[str] = Field(default_factory=list)
    # Graph steps taken after the plan was approved (deep mode's AGENT_DEEP_MAX_STEPS).
    steps: int = 0
    nodes: dict[str, NodeUsage] = Field(default_factory=dict)

    def node(self, name: str) -> NodeUsage:
        return self.nodes.setdefault(name, NodeUsage())

    def calls_left(self, budget: ModeBudget) -> int:
        return max(budget.max_model_calls - self.model_calls, 0)


class LimitReached(Exception):
    """A budget limit stops the node; the graph routes to `finalize_partial`."""

    def __init__(self, limit: LimitHit) -> None:
        super().__init__(limit)
        self.limit: LimitHit = limit


def remaining_seconds(deadline_at: datetime | None, clock: Clock = utcnow) -> float | None:
    """Seconds left before the absolute deadline; None when no deadline is set yet."""
    if deadline_at is None:
        return None
    return (deadline_at - clock()).total_seconds()


def take_tool_call(usage: Usage, budget: ModeBudget, node: str | None = None) -> None:
    if usage.tool_calls >= budget.max_tool_calls:
        raise LimitReached("tool_calls")
    usage.tool_calls += 1
    if node is not None:
        usage.node(node).tool_calls += 1


def take_sandbox_run(usage: Usage, budget: ModeBudget) -> None:
    if usage.sandbox_runs >= budget.max_sandbox_runs:
        raise LimitReached("sandbox_runs")
    usage.sandbox_runs += 1


async def guarded_call(
    router: Router,
    request: ChatRequest,
    task_class: TaskClass,
    usage: Usage,
    budget: ModeBudget,
    deadline_at: datetime | None,
    clock: Clock = utcnow,
    *,
    node: str | None = None,
    emit: Emit | None = None,
) -> ChatResponse:
    """One model call within the budget, or `LimitReached` without calling the model.

    The call is bounded by the time left until the absolute `deadline_at`, so a deadline
    restored from a checkpoint after a restart still holds.
    """
    if usage.model_calls >= budget.max_model_calls:
        raise LimitReached("model_calls")
    remaining = remaining_seconds(deadline_at, clock)
    if remaining is not None and remaining <= 0:
        raise LimitReached("deadline")
    usage.model_calls += 1
    if node is not None:
        usage.node(node).model_calls += 1
    try:
        response = await asyncio.wait_for(
            router.chat(request, task_class=task_class), timeout=remaining
        )
    except TimeoutError:
        raise LimitReached("deadline") from None
    usage.tokens_in += response.tokens_in
    usage.tokens_out += response.tokens_out
    usage.providers.append(response.provider)
    if emit is not None:
        await emit(
            "model_called",
            {
                "task_class": task_class,
                "provider": response.provider,
                "cache_hit": response.cache_hit,
                "fallback_from": ",".join(response.fallback_from) or None,
            },
        )
    return response
