"""RunService with the graph engine (T016): stub nodes, real Postgres, Redis and checkpoints."""

import os
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import Engine, select

from agent.budget import ModeBudget
from agent.checkpoint import open_checkpointer, setup_checkpointer
from agent.graph.build import build_graph
from agent.graph.context import GraphDeps, NodeContext, NodeFn
from agent.graph.state import Calculation, CalculationInput, ResearchState
from agent.loop import Budget
from agent.report import Report
from agent.runs import RunService, graph_config
from api import schema
from llm.testing import FakeRouter
from llm.types import ChatMessage, ChatRequest, ChatResponse, TaskClass
from tests.integration.agent.helpers import FACT, FakeTools, add_user, good_script, redis_client
from tests.unit.tools.test_documents import _registry
from tools.registry import GraphNode
from tools.types import SandboxUnavailable

QUESTION = "THYAO 2025 üçüncü çeyrek net kârı ne kadar?"
CALC = Calculation(
    cite_id="k:1",
    name="net_income_growth",
    description="Net kâr büyümesi",
    inputs=[CalculationInput(name="net_income", cite_id="f:1", value=Decimal("1234.5"))],
    code="result = {'net_income_growth': inputs['net_income'] / 1000 - 1}",
    output=Decimal("0.2345"),
    verified=True,
)
REPORT = Report.model_validate(
    {
        "language": "tr",
        "status": "completed",
        "summary": "Net kâr 1.234,5 milyon TL oldu [1].",
        "points": [{"text": "Büyüme %23,45", "cite_ids": ["k:1"]}],
        "tables": [
            {
                "title": "Net kâr",
                "columns": ["Dönem", "Net kâr"],
                "rows": [[{"text": "2025-Q3"}, {"text": "1.234,5", "cite_id": "f:1"}]],
            }
        ],
        "citations": [{"marker": 1, "cite_id": "f:1"}],
        "disclaimer": "model text",
    }
)


def _url() -> str:
    return os.environ.get(
        "DATABASE_URL", "postgresql://disclosly:CHANGE_ME@localhost:5432/disclosly"
    )


@pytest.fixture
async def redis() -> AsyncIterator[Any]:
    async for client in redis_client():
        yield client


@pytest.fixture
async def saver(engine: Engine) -> AsyncIterator[AsyncPostgresSaver]:
    await setup_checkpointer(_url())
    async with open_checkpointer(_url()) as checkpointer:
        yield checkpointer


def _node(name: str, update: dict[str, Any] | None = None) -> NodeFn:
    async def run(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
        await ctx.emit("step_started", {"step_id": None, "summary": name})
        return dict(update or {})

    return run


async def _router(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
    await ctx.call(ChatRequest(messages=[ChatMessage(role="user", content="q")]), "cheap")
    await ctx.emit("mode_selected", {"mode": "simple", "uses_watchlist": False})
    return {"mode": "simple", "language": "tr", "budget": ModeBudget.simple()}


async def _retriever(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
    await ctx.emit("tool_called", {"tool": "get_financial_facts", "summary": "THYAO"})
    return {"evidence": [FACT], "calculations": [CALC]}


class OneAnswer:
    async def chat(self, request: ChatRequest, *, task_class: TaskClass) -> ChatResponse:
        return ChatResponse(content="{}", provider="groq", model="m")


def _service(
    engine: Engine,
    redis: Any,
    saver: AsyncPostgresSaver,
    nodes: dict[GraphNode, NodeFn],
    router: Any = None,
) -> RunService:
    router = router or OneAnswer()
    service = RunService(
        engine,
        redis,
        router,
        FakeTools({"get_financial_facts": [FACT]}),
        Budget(),
        agent_engine="graph",
    )
    registry, _ = _registry([])
    deps = GraphDeps(
        router=router, tools=registry, emit=service.emit_event, attempts=service.attempt
    )
    service.attach_graph(build_graph(deps, saver, nodes))
    return service


NODES: dict[GraphNode, NodeFn] = {
    "router": _router,
    "retriever_simple": _retriever,
    "writer_simple": _node("writer_simple", {"draft": REPORT}),
}


async def _events(service: RunService, run_id: uuid.UUID) -> list[tuple[str, dict[str, Any]]]:
    return [(e.event, e.data) async for e in service.events(run_id, block_ms=100) if e is not None]


async def test_graph_run_streams_and_persists(
    engine: Engine, redis: Any, saver: AsyncPostgresSaver
) -> None:
    user = add_user(engine)
    service = _service(engine, redis, saver, NODES)

    run_id = await service.create(user, QUESTION)
    await service.wait_idle()

    events = await _events(service, run_id)
    assert [e for e, _ in events] == [
        "run_started",
        "model_called",
        "mode_selected",
        "tool_called",
        "step_started",
        "final",
    ]
    assert events[0][1] == {"run_id": str(run_id), "language": "tr", "node": None, "attempt": 1}
    assert [(d["node"], d["attempt"]) for _, d in events[1:-1]] == [
        ("router", 1),
        ("router", 1),
        ("retriever_simple", 1),
        ("writer_simple", 1),
    ]
    final = events[-1][1]
    assert final["status"] == "completed"
    assert final["answer"]["tables"][0]["rows"][0][1]["cite_id"] == "f:1"
    assert final["answer"]["disclaimer"] == "Yatırım tavsiyesi değildir / Not investment advice"

    run = await service.get(run_id, user)
    assert run is not None
    assert (run["status"], run["engine"], run["mode"]) == ("completed", "graph", "simple")
    assert Report.model_validate(run["answer"]).tables[0].title == "Net kâr"
    assert run["usage"]["model_calls"] == 1
    assert set(run["usage"]["nodes"]) == {"router", "retriever_simple", "writer_simple"}
    assert run["usage"]["nodes"]["router"]["model_calls"] == 1
    cites = {c["cite_id"]: c for c in run["citations"]}
    assert set(cites) == {"f:1", "k:1"}
    calc = cites["k:1"]
    assert calc["excerpt"] == "Net kâr büyümesi (net_income_growth)"
    assert calc["source"] == {
        "kind": "calculation",
        "name": "net_income_growth",
        "inputs": [{"name": "net_income", "cite_id": "f:1", "value": "1234.5"}],
        "code": CALC.code,
        "output": "0.2345",
    }

    # The run is checkpointed in `agent_graph` under its own id.
    snapshot = await service.graph.aget_state(graph_config(run_id))  # type: ignore[union-attr]
    assert snapshot.values["draft"].status == "completed"


async def test_missing_nodes_finish_partial(
    engine: Engine, redis: Any, saver: AsyncPostgresSaver
) -> None:
    user = add_user(engine)
    service = _service(engine, redis, saver, {"router": _router})

    run_id = await service.create(user, QUESTION)
    await service.wait_idle()

    run = await service.get(run_id, user)
    assert run is not None
    assert run["status"] == "partial"
    assert run["answer"]["confidence"]["level"] == "low"
    events = await _events(service, run_id)
    assert events[-1][0] == "final"


async def test_failure_records_error_and_refunds_before_a_model_call(
    engine: Engine, redis: Any, saver: AsyncPostgresSaver
) -> None:
    async def broken(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
        raise SandboxUnavailable

    refunds: list[str] = []

    async def refund() -> None:
        refunds.append("refund")

    user = add_user(engine)
    service = _service(engine, redis, saver, {"router": broken})
    run_id = await service.create(user, QUESTION, on_early_failure=refund)
    await service.wait_idle()

    run = await service.get(run_id, user)
    assert run is not None
    assert (run["status"], run["error_code"]) == ("failed", "sandbox_unavailable")
    events = await _events(service, run_id)
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "sandbox_unavailable"
    assert events[-1][1]["node"] is None
    assert refunds == ["refund"]


async def test_single_engine_unchanged(
    engine: Engine, redis: Any, saver: AsyncPostgresSaver
) -> None:
    user = add_user(engine)
    service = _service(engine, redis, saver, NODES, router=good_script())
    service.agent_engine = "single"

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
    assert all("node" not in data for _, data in events)
    with engine.connect() as conn:
        row = conn.execute(
            select(schema.runs.c.engine, schema.runs.c.mode).where(schema.runs.c.id == run_id)
        ).one()
    assert tuple(row) == ("single", None)


async def test_without_a_graph_runs_use_the_single_engine(engine: Engine, redis: Any) -> None:
    user = add_user(engine)
    service = RunService(
        engine,
        redis,
        good_script(),
        FakeTools({"get_financial_facts": [FACT]}),
        Budget(),
        agent_engine="graph",
    )
    run_id = await service.create(user, QUESTION)
    await service.wait_idle()
    run = await service.get(run_id, user)
    assert run is not None
    assert (run["engine"], run["status"]) == ("single", "completed")


async def test_attempt_counter(redis: Any) -> None:
    service = RunService(None, redis, FakeRouter(), FakeTools(), Budget())  # type: ignore[arg-type]
    run_id = str(uuid.uuid4())
    assert await service.attempt(run_id, "router", 1) == 1
    assert await service.attempt(run_id, "router", 1) == 2
    assert await service.attempt(run_id, "router", 2) == 1
    assert 0 < await redis.ttl(f"run:{run_id}:attempt:router:1") <= 48 * 3600
