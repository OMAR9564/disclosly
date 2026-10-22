"""What graph nodes receive: shared dependencies and a per-execution context.

Nodes are plain async functions `(state, ctx) -> update`. The node wrapper in
`agent/graph/build.py` builds the `NodeContext` (node name, attempt, a working copy of the
usage) and stamps `node` and `attempt` on every event the node emits
(contracts/sse-events.md).
"""

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from agent.budget import Clock, Mode, ModeBudget, Usage, guarded_call, utcnow
from agent.graph.state import ResearchState
from llm.router import Router
from llm.types import ChatRequest, ChatResponse, TaskClass
from tools.python import Sandbox
from tools.registry import GraphNode, NodeTools
from tools.types import SeenItem

EmitEvent = Callable[[str, str, dict[str, Any]], Awaitable[None]]
"""`(run_id, event, data)`: appends one event to the run's stream."""

AttemptCounter = Callable[[str, str, int], Awaitable[int]]
"""`(run_id, node, graph step)`: 1 for a node's first execution at that step, +1 per re-run."""


class NodeToolsSource(Protocol):
    def for_node(self, node: GraphNode) -> NodeTools: ...


class DeepQuota(Protocol):
    """Reads the deep quota without taking it (quota_gate); the API takes it at approval."""

    async def deep_remaining(self, user_id: str) -> int: ...


class CitationReloader(Protocol):
    """Reads citations again from the corpus for the verifier (tools/citations.py)."""

    async def reload(self, cite_ids: Iterable[str]) -> dict[str, SeenItem]: ...


class RunStore(Protocol):
    """The run row updates a node makes before pausing (decision)."""

    async def mark_awaiting(
        self, run_id: str, plan: dict[str, Any] | None, expires_at: datetime
    ) -> bool: ...


async def _first_attempt(run_id: str, node: str, step: int) -> int:
    return 1


async def _no_events(run_id: str, event: str, data: dict[str, Any]) -> None:
    return None


@dataclass
class GraphDeps:
    router: Router
    tools: NodeToolsSource
    budgets: Mapping[Mode, ModeBudget] = field(
        default_factory=lambda: {"simple": ModeBudget.simple(), "deep": ModeBudget.deep()}
    )
    emit: EmitEvent = _no_events
    attempts: AttemptCounter = _first_attempt
    clock: Clock = utcnow
    plan_max_steps: int = 8
    plan_ttl_hours: int = 24
    verify_max_rounds: int = 2
    quota: DeepQuota | None = None
    runs: RunStore | None = None
    # The calculation sandbox (analyst and verifier); None where no calculation can run.
    sandbox: Sandbox | None = None
    # The verifier's input check reads citations again; None falls back to the run's evidence.
    citations: CitationReloader | None = None


@dataclass
class NodeContext:
    node: GraphNode
    attempt: int
    deps: GraphDeps
    run_id: str
    usage: Usage
    budget: ModeBudget
    deadline_at: datetime | None

    async def emit(self, event: str, data: dict[str, Any]) -> None:
        await self.deps.emit(
            self.run_id, event, {**data, "node": self.node, "attempt": self.attempt}
        )

    async def call(
        self, request: ChatRequest, task_class: TaskClass, *, budget: ModeBudget | None = None
    ) -> ChatResponse:
        """One model call within the run's budget and deadline (raises `LimitReached`)."""
        return await guarded_call(
            self.deps.router,
            request,
            task_class,
            self.usage,
            budget or self.budget,
            self.deadline_at,
            self.deps.clock,
            node=self.node,
            emit=self.emit,
        )

    def tools(self) -> NodeTools:
        return self.deps.tools.for_node(self.node)


NodeFn = Callable[[ResearchState, NodeContext], Awaitable[dict[str, Any]]]
