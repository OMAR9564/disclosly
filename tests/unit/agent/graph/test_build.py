"""agent.graph.build (T015) with stub nodes: routing, limits, retries, the node wrapper."""

import uuid
from decimal import Decimal
from typing import Any, cast

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent.budget import LimitReached, ModeBudget, Usage
from agent.checkpoint import graph_serde
from agent.graph.build import DURABILITY, Routes, build_graph
from agent.graph.context import NodeContext, NodeFn
from agent.graph.nodes.finalize_partial import partial_report
from agent.graph.state import (
    Calculation,
    Finding,
    MemoryIntent,
    ResearchState,
    RouterDecision,
    VerificationResult,
)
from agent.plan import Plan
from agent.report import Report
from llm.testing import FakeRouter
from retrieval.types import SearchUnavailable
from tests.unit.agent.graph.helpers import Events, make_deps
from tools.registry import GraphNode
from tools.types import SandboxUnavailable, SeenItem

PLAN = Plan.model_validate({"steps": [{"id": "s1", "kind": "facts", "goal": "Net borç"}]})
FACT = SeenItem(
    cite_id="f:1", text="Net kâr: 1.234,5 milyon TRY", value=Decimal("1234.5"), source={}
)


class Trace:
    def __init__(self) -> None:
        self.visited: list[str] = []

    def node(self, name: str, update: dict[str, Any] | None = None) -> NodeFn:
        async def run(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
            self.visited.append(name)
            await ctx.emit("step_started", {"step_id": None, "summary": name})
            return dict(update or {})

        return run


def _report(status: Any = "completed") -> Report:
    return Report(language="en", status=status, summary="done")


async def _run(
    nodes: dict[GraphNode, NodeFn], state: dict[str, Any] | None = None, **deps: Any
) -> tuple[dict[str, Any], Events]:
    events = Events()
    graph = build_graph(
        make_deps(FakeRouter(), events, **deps), InMemorySaver(serde=graph_serde()), nodes
    )
    config: Any = {"configurable": {"thread_id": str(uuid.uuid4())}}
    initial = {"run_id": "run-1", "user_id": "u", "question": "q", "errors": [], **(state or {})}
    result = await graph.ainvoke(cast(ResearchState, initial), config, durability=DURABILITY)
    return dict(result), events


async def test_simple_route() -> None:
    trace = Trace()
    nodes: dict[GraphNode, NodeFn] = {
        "router": trace.node("router", {"mode": "simple", "language": "en"}),
        "retriever_simple": trace.node("retriever_simple"),
        "writer_simple": trace.node("writer_simple", {"draft": _report()}),
    }
    result, events = await _run(nodes)

    assert trace.visited == ["router", "retriever_simple", "writer_simple"]
    assert result["draft"].status == "completed"
    # Every event carries the node and its attempt.
    assert [(d["node"], d["attempt"]) for _, _, d in events.events] == [
        ("router", 1),
        ("retriever_simple", 1),
        ("writer_simple", 1),
    ]
    assert set(result["usage"].nodes) == {"router", "retriever_simple", "writer_simple"}


async def test_deep_route_with_approved_plan() -> None:
    trace = Trace()
    nodes: dict[GraphNode, NodeFn] = {
        "router": trace.node("router", {"mode": "deep", "budget": ModeBudget.deep()}),
        "quota_gate": trace.node("quota_gate"),
        "planner": trace.node("planner", {"plan": PLAN}),
        "decision": trace.node("decision", {"plan_approved": True}),
        "retriever": trace.node("retriever"),
        "analyst": trace.node("analyst"),
        "verifier": trace.node(
            "verifier", {"verification": VerificationResult(round=1, passed=True)}
        ),
        "writer": trace.node("writer", {"draft": _report()}),
    }
    result, _ = await _run(nodes)

    assert trace.visited == [
        "router",
        "quota_gate",
        "planner",
        "decision",
        "retriever",
        "analyst",
        "verifier",
        "writer",
    ]
    # Steps after approval are counted toward AGENT_DEEP_MAX_STEPS.
    assert result["usage"].steps == 4


async def test_cancel_ends_without_a_draft() -> None:
    trace = Trace()
    nodes: dict[GraphNode, NodeFn] = {
        "router": trace.node("router", {"mode": "deep"}),
        "quota_gate": trace.node("quota_gate"),
        "planner": trace.node("planner", {"plan": PLAN}),
        "decision": trace.node("decision", {"cancelled": True}),
        "retriever": trace.node("retriever"),
    }
    result, _ = await _run(nodes)

    assert trace.visited == ["router", "quota_gate", "planner", "decision"]
    assert result.get("draft") is None


async def test_quota_offer_accepted_as_simple() -> None:
    trace = Trace()
    nodes: dict[GraphNode, NodeFn] = {
        "router": trace.node("router", {"mode": "deep"}),
        "quota_gate": trace.node("quota_gate", {"decision_kind": "deep_quota_offer"}),
        "planner": trace.node("planner", {"plan": PLAN}),
        "decision": trace.node("decision", {"mode": "simple"}),
        "retriever_simple": trace.node("retriever_simple"),
        "writer_simple": trace.node("writer_simple", {"draft": _report()}),
    }
    await _run(nodes)

    assert trace.visited == [
        "router",
        "quota_gate",
        "decision",
        "retriever_simple",
        "writer_simple",
    ]


async def test_verifier_failure_revises_then_finalizes() -> None:
    trace = Trace()
    rounds = iter([1, 2])

    async def verifier(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
        trace.visited.append("verifier")
        number = next(rounds)
        return {
            "verification": VerificationResult(round=number, passed=False),
            "verify_rounds": number,
        }

    nodes: dict[GraphNode, NodeFn] = {
        "router": trace.node("router", {"mode": "deep", "budget": ModeBudget.deep()}),
        "quota_gate": trace.node("quota_gate"),
        "planner": trace.node("planner", {"plan": PLAN}),
        "decision": trace.node("decision", {"plan_approved": True}),
        "retriever": trace.node("retriever"),
        "analyst": trace.node("analyst"),
        "verifier": verifier,
        "writer": trace.node("writer", {"draft": _report()}),
    }
    result, _ = await _run(nodes)

    assert trace.visited == [
        "router",
        "quota_gate",
        "planner",
        "decision",
        "retriever",
        "analyst",
        "verifier",
        # Revision: back to the planner, then straight to retrieval (no new approval).
        "planner",
        "retriever",
        "analyst",
        "verifier",
    ]
    assert result["draft"].status == "partial"


async def test_analyst_goes_to_writer_until_the_verifier_exists() -> None:
    trace = Trace()
    nodes: dict[GraphNode, NodeFn] = {
        "router": trace.node("router", {"mode": "deep"}),
        "quota_gate": trace.node("quota_gate"),
        "planner": trace.node("planner", {"plan": PLAN}),
        "decision": trace.node("decision", {"plan_approved": True}),
        "retriever": trace.node("retriever"),
        "analyst": trace.node("analyst"),
        "writer": trace.node("writer", {"draft": _report()}),
    }
    await _run(nodes)
    assert trace.visited[-2:] == ["analyst", "writer"]


async def test_missing_nodes_route_to_finalize_partial() -> None:
    trace = Trace()
    result, _ = await _run({"router": trace.node("router", {"mode": "simple"})})

    assert trace.visited == ["router"]
    assert result["draft"].status == "partial"
    assert result["draft"].confidence.level == "low"


async def test_limit_routes_to_finalize_partial() -> None:
    trace = Trace()

    async def retriever_simple(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
        trace.visited.append("retriever_simple")
        ctx.usage.model_calls = 4
        raise LimitReached("model_calls")

    nodes: dict[GraphNode, NodeFn] = {
        "router": trace.node("router", {"mode": "simple", "language": "en"}),
        "retriever_simple": retriever_simple,
        "writer_simple": trace.node("writer_simple", {"draft": _report()}),
    }
    result, _ = await _run(nodes)

    assert trace.visited == ["router", "retriever_simple"]
    assert result["limit_hit"] == "model_calls"
    assert result["usage"].model_calls == 4
    draft = result["draft"]
    assert draft.status == "partial"
    assert draft.summary.startswith("No answer could be verified")
    assert draft.confidence.note == "The research reached its model call limit."


async def test_deep_step_limit_after_approval() -> None:
    trace = Trace()
    small = ModeBudget(
        mode="deep", max_model_calls=20, deadline_seconds=300, max_tool_calls=24, max_steps=2
    )
    nodes: dict[GraphNode, NodeFn] = {
        "router": trace.node("router", {"mode": "deep", "budget": small}),
        "quota_gate": trace.node("quota_gate"),
        "planner": trace.node("planner", {"plan": PLAN}),
        "decision": trace.node("decision", {"plan_approved": True}),
        "retriever": trace.node("retriever"),
        "analyst": trace.node("analyst"),
        "writer": trace.node("writer", {"draft": _report()}),
    }
    result, _ = await _run(nodes)

    assert trace.visited[-2:] == ["retriever", "analyst"]
    assert result["limit_hit"] == "steps"
    assert result["draft"].status == "partial"


@pytest.mark.parametrize("error", [SearchUnavailable, SandboxUnavailable])
async def test_transient_errors_are_retried_once(error: type[Exception]) -> None:
    calls = 0
    attempts: list[int] = []

    async def flaky(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        attempts.append(ctx.attempt)
        if calls == 1:
            raise error("down")
        return {"draft": _report()}

    counter: dict[tuple[str, str, int], int] = {}

    async def attempt(run_id: str, node: str, step: int) -> int:
        counter[(run_id, node, step)] = counter.get((run_id, node, step), 0) + 1
        return counter[(run_id, node, step)]

    nodes: dict[GraphNode, NodeFn] = {
        "router": Trace().node("router", {"mode": "simple"}),
        "retriever_simple": Trace().node("retriever_simple"),
        "writer_simple": flaky,
    }
    result, _ = await _run(nodes, attempts=attempt)

    assert calls == 2
    assert attempts == [1, 2]
    assert result["draft"].status == "completed"


async def test_other_errors_are_not_retried() -> None:
    calls = 0

    async def broken(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise RuntimeError("bug")

    with pytest.raises(RuntimeError):
        await _run({"router": broken})
    assert calls == 1


async def test_memory_step_runs_before_the_mode_route() -> None:
    trace = Trace()
    decision = RouterDecision(
        mode="simple", memory_intent=MemoryIntent(action="watchlist_add", ticker="AAPL")
    )
    nodes: dict[GraphNode, NodeFn] = {
        "router": trace.node("router", {"mode": "simple", "router": decision}),
        "memory": trace.node("memory"),
        "retriever_simple": trace.node("retriever_simple"),
        "writer_simple": trace.node("writer_simple", {"draft": _report()}),
    }
    await _run(nodes)
    assert trace.visited == ["router", "memory", "retriever_simple", "writer_simple"]


def test_graph_needs_a_router() -> None:
    with pytest.raises(ValueError, match="router"):
        build_graph(make_deps(FakeRouter()), None, {})


def test_routes_fall_back_for_missing_nodes() -> None:
    routes = Routes({"router": Trace().node("router")}, verify_max_rounds=2)
    assert routes.after_quota_gate({}) == "finalize_partial"
    assert routes.after_planner({"plan": PLAN}) == "finalize_partial"
    assert routes.after_decision({"plan_approved": False}) == "finalize_partial"
    assert routes.after_verifier({}) == "finalize_partial"
    assert routes.after_writer({"draft": None}) == "finalize_partial"
    assert routes.mode({"refused": True, "mode": "deep"}) == "finalize_partial"


def test_partial_report_keeps_only_checked_findings() -> None:
    calc = Calculation(
        cite_id="k:1", name="growth", description="Net kâr büyümesi", code="x", output=None
    )
    state: ResearchState = {
        "language": "tr",
        "evidence": [FACT],
        "findings": [
            Finding(id="F1", text="Net kâr 1.234,5 milyon TL", cite_ids=["f:1"]),
            Finding(id="F2", text="Net kâr 2.000 milyon TL", cite_ids=["f:1"]),
            Finding(id="F3", text="Gelir arttı", cite_ids=["c:missing"]),
        ],
        "calculations": [calc],
        "limit_hit": "deadline",
        "usage": Usage(),
    }
    report = partial_report(state)

    assert report.status == "partial"
    assert [p.text for p in report.points] == ["Net kâr 1.234,5 milyon TL"]
    assert report.unverified == ["Net kâr 2.000 milyon TL", "Gelir arttı", "Net kâr büyümesi"]
    assert report.confidence is not None
    assert report.confidence.note == "Araştırma süre sınırına ulaştı."
    assert report.summary.startswith("Araştırma süre sınırına ulaştı.")


def test_partial_report_for_an_advice_question_is_refused() -> None:
    report = partial_report({"question": "Should I buy AAPL?", "refused": True})
    assert report.status == "refused"
    assert report.language == "en"
    assert report.tables == []
