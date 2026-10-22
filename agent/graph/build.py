"""The research graph (research §R2, contracts/agent-graph.md).

```text
START → router ─┬─ (memory_intent) → memory ─┐
                ├─ refused / simple ─────────┴→ retriever_simple → writer_simple → END
                └─ deep → quota_gate ─┬─ (deep quota 0) → decision[offer]
                                      └─ planner → decision[plan]
decision[offer] ─┬─ simple → retriever_simple
                 └─ cancel → END
decision[plan] ─┬─ approve/edit → retriever → analyst
                └─ cancel → END
analyst → verifier ─┬─ pass → writer → END
                    └─ fail (round < max) → planner (revision, no new approval)
Any node: limit hit, missing next node or no draft → finalize_partial → END
```

Nodes arrive with the tasks that build them; until then their place routes to
`finalize_partial` (an optional `memory` step and the `verifier` are skipped instead). Every
node runs inside a wrapper that counts usage per node, opens the `agent.node` span, enforces
the deep step limit after approval and turns `LimitReached` into `limit_hit`.
"""

import time
from collections.abc import Callable, Mapping
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Durability, RetryPolicy

from agent.budget import LimitReached, Usage
from agent.graph.context import GraphDeps, NodeContext, NodeFn
from agent.graph.nodes.analyst import analyst_node
from agent.graph.nodes.decision import decision_node
from agent.graph.nodes.finalize_partial import finalize_partial_node
from agent.graph.nodes.planner import planner_node
from agent.graph.nodes.quota_gate import quota_gate_node
from agent.graph.nodes.retriever import retriever_node
from agent.graph.nodes.router import router_node
from agent.graph.nodes.writer import writer_node
from agent.graph.state import ResearchState
from agent.telemetry import node_span
from retrieval.types import SearchUnavailable
from tools.registry import GraphNode
from tools.types import SandboxUnavailable

FINALIZE = "finalize_partial"
# Transient tool failures only; model fallback stays inside the model router.
RETRY = RetryPolicy(
    max_attempts=2,
    initial_interval=0.5,
    jitter=True,
    retry_on=(SearchUnavailable, SandboxUnavailable),
)
# Checkpoint before the next node starts, so a restart resumes after the last finished node.
DURABILITY: Durability = "sync"

DEFAULT_NODES: dict[GraphNode, NodeFn] = {
    "router": router_node,
    # Deep path (US1).
    "quota_gate": quota_gate_node,
    "planner": planner_node,
    "decision": decision_node,
    "retriever": retriever_node,
    "analyst": analyst_node,
    "writer": writer_node,
    "finalize_partial": finalize_partial_node,
}

Graph = CompiledStateGraph[ResearchState, None, ResearchState, ResearchState]


def wrap(name: GraphNode, fn: NodeFn, deps: GraphDeps) -> Callable[..., Any]:
    """The node as LangGraph runs it: usage, span, step limit, attempt and limit handling."""

    async def run(state: ResearchState, config: RunnableConfig) -> dict[str, Any]:
        run_id = state.get("run_id", "")
        step = int((config.get("metadata") or {}).get("langgraph_step", 0))
        attempt = await deps.attempts(run_id, name, step)
        usage = (state.get("usage") or Usage()).model_copy(deep=True)
        budget = state.get("budget") or deps.budgets["simple"]

        if state.get("plan_approved") and budget.max_steps is not None and name != FINALIZE:
            if usage.steps >= budget.max_steps:
                return {"usage": usage, "limit_hit": "steps"}
            usage.steps += 1

        ctx = NodeContext(
            node=name,
            attempt=attempt,
            deps=deps,
            run_id=run_id,
            usage=usage,
            budget=budget,
            deadline_at=state.get("deadline_at"),
        )
        started = time.monotonic()
        with node_span(
            node=name, attempt=attempt, run_id=run_id, user_id=state.get("user_id", "")
        ) as span:
            try:
                update = await fn(state, ctx)
            except LimitReached as exc:
                update = {"limit_hit": exc.limit}
            finally:
                usage.node(name).ms += round((time.monotonic() - started) * 1000, 1)
                span.set_attribute("disclosly.model_calls", usage.model_calls)
        return {**update, "usage": ctx.usage}

    return run


class Routes:
    """Conditional edges. `go` falls back to `finalize_partial` for a node not built yet."""

    def __init__(self, nodes: Mapping[GraphNode, NodeFn], verify_max_rounds: int) -> None:
        self._nodes = nodes
        self._verify_max_rounds = verify_max_rounds

    def go(self, name: str) -> str:
        return name if name in self._nodes else FINALIZE

    def mode(self, state: ResearchState) -> str:
        if state.get("limit_hit"):
            return FINALIZE
        if state.get("mode") == "deep" and not state.get("refused"):
            return self.go("quota_gate")
        return self.go("retriever_simple")

    def after_router(self, state: ResearchState) -> str:
        router = state.get("router")
        if (
            not state.get("limit_hit")
            and router is not None
            and router.memory_intent is not None
            and "memory" in self._nodes
        ):
            return "memory"
        return self.mode(state)

    def after_quota_gate(self, state: ResearchState) -> str:
        if state.get("limit_hit"):
            return FINALIZE
        if state.get("decision_kind") == "deep_quota_offer":
            return self.go("decision")
        return self.go("planner")

    def after_planner(self, state: ResearchState) -> str:
        if state.get("limit_hit") or state.get("plan") is None:
            return FINALIZE
        # A verifier revision stays within the approved scope and needs no new approval.
        return self.go("retriever" if state.get("plan_approved") else "decision")

    def after_decision(self, state: ResearchState) -> str:
        if state.get("cancelled"):
            return END
        if state.get("limit_hit"):
            return FINALIZE
        if state.get("mode") == "simple":
            return self.go("retriever_simple")
        return self.go("retriever") if state.get("plan_approved") else FINALIZE

    def after_retriever(self, state: ResearchState) -> str:
        return FINALIZE if state.get("limit_hit") else self.go("analyst")

    def after_analyst(self, state: ResearchState) -> str:
        if state.get("limit_hit"):
            return FINALIZE
        return "verifier" if "verifier" in self._nodes else self.go("writer")

    def after_verifier(self, state: ResearchState) -> str:
        verification = state.get("verification")
        if state.get("limit_hit") or verification is None:
            return FINALIZE
        if verification.passed:
            return self.go("writer")
        if state.get("verify_rounds", 0) < self._verify_max_rounds:
            return self.go("planner")
        return FINALIZE

    def after_retriever_simple(self, state: ResearchState) -> str:
        return FINALIZE if state.get("limit_hit") else self.go("writer_simple")

    def after_writer(self, state: ResearchState) -> str:
        if state.get("limit_hit") or state.get("draft") is None:
            return FINALIZE
        return END


def build_graph(
    deps: GraphDeps,
    checkpointer: BaseCheckpointSaver[Any] | None,
    nodes: Mapping[GraphNode, NodeFn] | None = None,
) -> Graph:
    registered: dict[GraphNode, NodeFn] = dict(DEFAULT_NODES if nodes is None else nodes)
    registered.setdefault("finalize_partial", finalize_partial_node)
    if "router" not in registered:
        raise ValueError("the graph needs a router node")
    routes = Routes(registered, deps.verify_max_rounds)

    builder = StateGraph(ResearchState)
    for name, fn in registered.items():
        builder.add_node(name, wrap(name, fn, deps), retry_policy=RETRY)
    builder.add_edge(START, "router")

    edges: dict[str, Callable[[ResearchState], str]] = {
        "router": routes.after_router,
        "memory": routes.mode,
        "quota_gate": routes.after_quota_gate,
        "planner": routes.after_planner,
        "decision": routes.after_decision,
        "retriever": routes.after_retriever,
        "analyst": routes.after_analyst,
        "verifier": routes.after_verifier,
        "retriever_simple": routes.after_retriever_simple,
        "writer": routes.after_writer,
        "writer_simple": routes.after_writer,
    }
    targets = [*registered, END]
    for source, route in edges.items():
        if source in registered:
            builder.add_conditional_edges(source, route, targets)
    builder.add_edge(FINALIZE, END)
    return builder.compile(checkpointer=checkpointer)
