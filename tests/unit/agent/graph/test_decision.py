"""decision node (T019) in a graph with an in-memory checkpointer."""

import uuid
from datetime import datetime, timedelta
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.budget import ModeBudget, Usage
from agent.checkpoint import graph_serde
from agent.graph.build import DURABILITY, Graph, build_graph
from agent.graph.context import NodeContext, NodeFn
from agent.graph.nodes.decision import ALLOWED_ACTIONS, Decision, decision_node
from agent.graph.state import ResearchState
from agent.plan import Plan
from agent.report import Report
from llm.testing import FakeRouter
from tests.unit.agent.graph.helpers import T0, Events, make_deps
from tools.registry import GraphNode

PLAN = Plan.model_validate(
    {
        "steps": [
            {
                "id": "s1",
                "kind": "facts",
                "goal": "Net borç ve FAVÖK",
                "tickers": ["THYAO"],
                "periods": ["2025-Q4"],
                "metrics": ["ebitda"],
            }
        ],
        "estimate": {"typical": 6, "max": 15},
    }
)
DECIDED = T0 + timedelta(minutes=5)


class FakeRuns:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None, datetime]] = []
        self.decided = False

    async def mark_awaiting(
        self, run_id: str, plan: dict[str, Any] | None, expires_at: datetime
    ) -> bool:
        self.calls.append((run_id, plan, expires_at))
        return not self.decided


class FakeQuota:
    async def deep_remaining(self, user_id: str) -> int:
        return 4


def _stub(update: dict[str, Any] | None = None) -> NodeFn:
    async def run(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
        return dict(update or {})

    return run


class Harness:
    def __init__(self, decision_kind: str | None = None) -> None:
        self.events = Events()
        self.runs = FakeRuns()
        self.visited: list[str] = []
        deps = make_deps(FakeRouter(), self.events, runs=self.runs, quota=FakeQuota())

        def seen(name: str, update: dict[str, Any] | None = None) -> NodeFn:
            async def run(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
                self.visited.append(name)
                return dict(update or {})

            return run

        nodes: dict[GraphNode, NodeFn] = {
            "router": _stub({"mode": "deep", "budget": ModeBudget.deep(), "language": "tr"}),
            "quota_gate": _stub({"decision_kind": decision_kind}),
            "planner": _stub({"plan": PLAN}),
            "decision": decision_node,
            "retriever": seen("retriever"),
            "analyst": seen("analyst"),
            "writer": seen(
                "writer", {"draft": Report(language="tr", status="completed", summary="ok")}
            ),
            "retriever_simple": seen("retriever_simple"),
            "writer_simple": seen(
                "writer_simple", {"draft": Report(language="tr", status="completed", summary="s")}
            ),
        }
        self.graph: Graph = build_graph(deps, InMemorySaver(serde=graph_serde()), nodes)
        self.config: RunnableConfig = {"configurable": {"thread_id": str(uuid.uuid4())}}

    async def start(self) -> None:
        state: Any = {
            "run_id": "run-1",
            "user_id": "user-1",
            "question": "THYAO net borç / FAVÖK",
            "usage": Usage(),
            "errors": [],
        }
        await self.graph.ainvoke(state, self.config, durability=DURABILITY)

    async def decide(self, **fields: Any) -> dict[str, Any]:
        self.runs.decided = True
        decision = Decision(decided_at=DECIDED, **fields).model_dump(mode="json")
        await self.graph.ainvoke(Command(resume=decision), self.config, durability=DURABILITY)
        return dict((await self.graph.aget_state(self.config)).values)


async def test_pauses_after_marking_the_row_and_emitting() -> None:
    harness = Harness()
    await harness.start()

    snapshot = await harness.graph.aget_state(harness.config)
    assert snapshot.next == ("decision",)
    assert harness.runs.calls == [("run-1", PLAN.model_dump(mode="json"), T0 + timedelta(hours=24))]
    awaiting = harness.events.data("awaiting_decision")
    assert awaiting == [
        {
            "kind": "plan",
            "plan": PLAN.model_dump(mode="json"),
            "estimate": {"typical": 6, "max": 15},
            "deep_remaining": 4,
            "expires_at": (T0 + timedelta(hours=24)).isoformat(),
            "node": "decision",
            "attempt": 1,
        }
    ]
    assert harness.visited == []


async def test_approve_sets_the_deep_deadline_and_continues() -> None:
    harness = Harness()
    await harness.start()

    values = await harness.decide(action="approve")

    assert values["plan_approved"] is True
    assert values["deadline_at"] == DECIDED + timedelta(seconds=300)
    assert values["plan"].steps == PLAN.steps
    assert harness.visited == ["retriever", "analyst", "writer"]
    # The re-run of the node on resume does not announce the decision again.
    assert harness.events.names().count("awaiting_decision") == 1
    assert harness.events.data("decision_made")[0]["action"] == "approve"


async def test_edit_replaces_the_plan() -> None:
    harness = Harness()
    await harness.start()
    edited = PLAN.model_copy(
        update={
            "steps": [
                PLAN.steps[0].model_copy(update={"id": "s7", "goal": "Yalnızca FAVÖK"}),
                PLAN.steps[0].model_copy(update={"id": "s9", "kind": "calculate"}),
            ]
        }
    )

    values = await harness.decide(action="edit", plan=edited)

    plan = values["plan"]
    assert [s.id for s in plan.steps] == ["s1", "s2"]
    assert plan.steps[0].goal == "Yalnızca FAVÖK"
    assert plan.edited is True
    assert plan.estimate is not None
    assert values["plan_approved"] is True


async def test_cancel_ends_the_run() -> None:
    harness = Harness()
    await harness.start()

    values = await harness.decide(action="cancel")

    assert values["cancelled"] is True
    assert harness.visited == []
    assert harness.events.names()[-2:] == ["decision_made", "run_cancelled"]
    assert (await harness.graph.aget_state(harness.config)).next == ()


async def test_quota_offer_accepted_as_simple() -> None:
    harness = Harness("deep_quota_offer")
    await harness.start()
    awaiting = harness.events.data("awaiting_decision")[0]
    assert (awaiting["kind"], awaiting["plan"], awaiting["estimate"]) == (
        "deep_quota_offer",
        None,
        None,
    )

    values = await harness.decide(action="simple")

    assert values["mode"] == "simple"
    assert values["deadline_at"] == DECIDED + timedelta(seconds=90)
    assert values["budget"].mode == "simple"
    assert harness.visited == ["retriever_simple", "writer_simple"]


def test_allowed_actions_per_kind() -> None:
    assert ALLOWED_ACTIONS["plan"] == {"approve", "edit", "cancel"}
    assert ALLOWED_ACTIONS["deep_quota_offer"] == {"simple", "cancel"}
