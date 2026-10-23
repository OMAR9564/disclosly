"""Restart tests (Phase 4 T041, research §R7): real Postgres, Redis and checkpoints.

Each test stops a graph run the way a deploy does (the run task is cancelled and its lease
released), starts a new `RunService` with a supervisor, and checks the run ends as an
uninterrupted run would: completed nodes make no new model or sandbox calls, re-run events
carry a higher `attempt`, and `usage.resumes` is recorded.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import Engine, insert, update

from agent.budget import ModeBudget, utcnow
from agent.checkpoint import open_checkpointer, setup_checkpointer
from agent.graph.build import build_graph
from agent.graph.context import GraphDeps, NodeContext, NodeFn
from agent.graph.state import ResearchState
from agent.loop import Budget
from agent.runs import RunService
from agent.supervisor import Leases, RunSupervisor, lease_key
from api import schema
from api.guards.quota import Quota
from llm.types import ChatMessage, ChatRequest, ChatResponse, TaskClass
from tests.integration.agent.helpers import FACT, FakeTools, add_user, redis_client
from tests.integration.agent.test_graph_runs import QUESTION, REPORT, _url
from tests.unit.tools.test_documents import _registry
from tools.registry import GraphNode

NODE_ORDER = ["router", "retriever_simple", "writer_simple"]


@pytest.fixture
async def redis() -> AsyncIterator[Any]:
    async for client in redis_client():
        yield client


@pytest.fixture
async def saver(engine: Engine) -> AsyncIterator[AsyncPostgresSaver]:
    await setup_checkpointer(_url())
    async with open_checkpointer(_url()) as checkpointer:
        yield checkpointer


@dataclass
class World:
    """What survives a restart in these tests: call counts and the stopping point."""

    stop_at: str | None = None
    reached: asyncio.Event = field(default_factory=asyncio.Event)
    model_calls: list[str] = field(default_factory=list)
    sandbox_calls: list[str] = field(default_factory=list)

    async def point(self, name: str) -> None:
        """Hang at `name` once (the process is about to be stopped there)."""
        if self.stop_at == name:
            self.stop_at = None
            self.reached.set()
            await asyncio.Event().wait()


class CountingRouter:
    def __init__(self, world: World) -> None:
        self.world = world

    async def chat(self, request: ChatRequest, *, task_class: TaskClass) -> ChatResponse:
        self.world.model_calls.append(task_class)
        await self.world.point("in_model")
        return ChatResponse(content="{}", provider="groq", model="m")


def _nodes(world: World) -> dict[GraphNode, NodeFn]:
    async def router(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
        await ctx.emit("step_started", {"step_id": None, "summary": "router"})
        await ctx.call(ChatRequest(messages=[ChatMessage(role="user", content="q")]), "cheap")
        return {"mode": "simple", "language": "tr", "budget": ModeBudget.simple()}

    async def retriever(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
        await ctx.emit("step_started", {"step_id": None, "summary": "retriever"})
        await world.point("after_router")
        world.sandbox_calls.append("run")
        await world.point("in_sandbox")
        return {"evidence": [FACT]}

    async def writer(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
        await ctx.emit("step_started", {"step_id": None, "summary": "writer"})
        await world.point("after_retriever")
        return {"draft": REPORT}

    return {"router": router, "retriever_simple": retriever, "writer_simple": writer}


def _service(engine: Engine, redis: Any, saver: AsyncPostgresSaver, world: World) -> RunService:
    router = CountingRouter(world)
    service = RunService(
        engine,
        redis,
        router,
        FakeTools({"get_financial_facts": [FACT]}),
        Budget(),
        agent_engine="graph",
        leases=Leases(redis),
    )
    registry, _ = _registry([])
    deps = GraphDeps(
        router=router, tools=registry, emit=service.emit_event, attempts=service.attempt
    )
    service.attach_graph(build_graph(deps, saver, _nodes(world)))
    return service


async def _events(service: RunService, run_id: uuid.UUID) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (e.id, e.event, e.data) async for e in service.events(run_id, block_ms=100) if e is not None
    ]


async def _stop_at(
    engine: Engine, redis: Any, saver: AsyncPostgresSaver, world: World, user: uuid.UUID
) -> uuid.UUID:
    """Start a run, wait for the stopping point, then stop the process as a deploy does."""
    first = _service(engine, redis, saver, world)
    run_id = await first.create(user, QUESTION)
    await asyncio.wait_for(world.reached.wait(), timeout=10)
    await first.cancel_graph_runs()
    assert not await redis.exists(lease_key(run_id))
    return run_id


async def _uninterrupted(
    engine: Engine, redis: Any, saver: AsyncPostgresSaver, user: uuid.UUID
) -> dict[str, Any]:
    world = World()
    service = _service(engine, redis, saver, world)
    run_id = await service.create(user, QUESTION)
    await service.wait_idle()
    run = await service.get(run_id, user)
    assert run is not None
    return {"status": run["status"], "model_calls": len(world.model_calls), "world": world}


@pytest.mark.parametrize(
    ("stop_at", "rerun_node", "completed_model_calls", "completed_sandbox_calls"),
    [
        ("after_router", "retriever_simple", 1, 0),
        ("in_model", "router", 0, 0),
        ("in_sandbox", "retriever_simple", 1, 0),
        ("after_retriever", "writer_simple", 1, 1),
    ],
)
async def test_restart_resumes_from_the_last_checkpoint(
    engine: Engine,
    redis: Any,
    saver: AsyncPostgresSaver,
    stop_at: str,
    rerun_node: str,
    completed_model_calls: int,
    completed_sandbox_calls: int,
) -> None:
    user = add_user(engine)
    baseline = await _uninterrupted(engine, redis, saver, user)
    quota = Quota(redis, 20, deep_limit=5)
    before = await quota.statuses(user)

    world = World(stop_at=stop_at)
    run_id = await _stop_at(engine, redis, saver, world, user)
    calls_before = (list(world.model_calls), list(world.sandbox_calls))
    assert len(calls_before[0]) >= completed_model_calls

    second = _service(engine, redis, saver, world)
    supervisor = RunSupervisor(second, second.leases)
    assert await supervisor.resume_orphans() == [run_id]
    await second.wait_idle()

    run = await second.get(run_id, user)
    assert run is not None
    assert run["status"] == baseline["status"] == "completed"
    assert run["usage"]["resumes"] == 1
    # Nodes that finished before the stop made no new calls; only the stopped node re-ran.
    new_model = world.model_calls[len(calls_before[0]) :]
    new_sandbox = world.sandbox_calls[len(calls_before[1]) :]
    assert len(world.model_calls) - len(new_model) >= completed_model_calls
    assert len(world.model_calls) == baseline["model_calls"] + (1 if stop_at == "in_model" else 0)
    expected_sandbox = len(baseline["world"].sandbox_calls) + (stop_at == "in_sandbox")
    assert len(world.sandbox_calls) == expected_sandbox
    if completed_sandbox_calls:
        assert new_sandbox == []
    assert await quota.statuses(user) == before

    events = await _events(second, run_id)
    ids = [event_id for event_id, _, _ in events]
    assert ids == sorted(ids, key=lambda i: tuple(int(p) for p in i.split("-")))
    resumed = [data for _, name, data in events if name == "run_resumed"]
    assert resumed == [{"from_node": rerun_node, "resume_count": 1, "node": None, "attempt": 1}]
    steps = [(d["node"], d["attempt"]) for _, name, d in events if name == "step_started"]
    assert (rerun_node, 2) in steps
    finished = NODE_ORDER[: NODE_ORDER.index(rerun_node)]
    assert all(attempt == 1 for node, attempt in steps if node in finished)
    assert events[-1][1] == "final"


async def test_two_supervisors_resume_a_run_once(
    engine: Engine, redis: Any, saver: AsyncPostgresSaver
) -> None:
    user = add_user(engine)
    world = World(stop_at="after_router")
    run_id = await _stop_at(engine, redis, saver, world, user)

    second = _service(engine, redis, saver, world)
    third = _service(engine, redis, saver, world)
    results = await asyncio.gather(
        RunSupervisor(second, second.leases).resume_orphans(),
        RunSupervisor(third, third.leases).resume_orphans(),
    )
    await asyncio.gather(second.wait_idle(), third.wait_idle())

    assert sorted(len(r) for r in results) == [0, 1]
    run = await second.get(run_id, user)
    assert run is not None
    assert (run["status"], run["usage"]["resumes"]) == ("completed", 1)
    events = await _events(second, run_id)
    assert [name for _, name, _ in events].count("run_resumed") == 1
    assert [name for _, name, _ in events].count("final") == 1


async def test_fourth_resume_fails_with_interrupted_limit(
    engine: Engine, redis: Any, saver: AsyncPostgresSaver
) -> None:
    user = add_user(engine)
    world = World(stop_at="after_router")
    run_id = await _stop_at(engine, redis, saver, world, user)
    with engine.begin() as conn:
        conn.execute(update(schema.runs).where(schema.runs.c.id == run_id).values(resume_count=3))

    second = _service(engine, redis, saver, world)
    assert await RunSupervisor(second, second.leases).resume_orphans() == []

    run = await second.get(run_id, user)
    assert run is not None
    assert (run["status"], run["error_code"]) == ("failed", "interrupted_limit")
    events = await _events(second, run_id)
    assert events[-1][1] == "error"
    assert events[-1][2]["code"] == "interrupted_limit"
    assert not await redis.exists(lease_key(run_id))


async def test_waiting_runs_are_not_resumed_and_expire(
    engine: Engine, redis: Any, saver: AsyncPostgresSaver
) -> None:
    user = add_user(engine)
    now = utcnow()
    with engine.begin() as conn:
        waiting, past = (
            conn.execute(
                insert(schema.runs)
                .values(
                    user_id=user,
                    question=QUESTION,
                    language="tr",
                    engine="graph",
                    status="awaiting_approval",
                    plan_expires_at=now + offset,
                )
                .returning(schema.runs.c.id)
            ).scalar_one()
            for offset in (timedelta(hours=1), -timedelta(minutes=1))
        )
    service = _service(engine, redis, saver, World())
    supervisor = RunSupervisor(service, Leases(redis, owner=f"t-{uuid.uuid4()}"), now=lambda: now)
    await redis.delete("supervisor:sweep")

    assert await supervisor.sweep() == []

    waiting_run = await service.get(waiting, user)
    expired_run = await service.get(past, user)
    assert waiting_run is not None and expired_run is not None
    assert waiting_run["status"] == "awaiting_approval"
    assert expired_run["status"] == "expired"
    events = await _events(service, past)
    assert [name for _, name, _ in events] == ["run_expired"]
    await redis.delete("supervisor:sweep")
