"""Shared fakes for graph node tests: a recording emitter and a node context factory."""

from datetime import UTC, datetime
from typing import Any

from agent.budget import ModeBudget, Usage
from agent.graph.context import GraphDeps, NodeContext
from llm.router import Router
from tests.unit.tools.test_documents import _registry
from tools.registry import GraphNode

T0 = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Events:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    async def __call__(self, run_id: str, event: str, data: dict[str, Any]) -> None:
        self.events.append((run_id, event, data))

    def names(self) -> list[str]:
        return [event for _, event, _ in self.events]

    def data(self, name: str) -> list[dict[str, Any]]:
        return [data for _, event, data in self.events if event == name]


def make_deps(router: Router, events: Events | None = None, **fields: Any) -> GraphDeps:
    registry, _ = _registry([])
    return GraphDeps(
        router=router, tools=registry, emit=events or Events(), clock=Clock(), **fields
    )


def make_ctx(
    deps: GraphDeps,
    node: GraphNode,
    *,
    budget: ModeBudget | None = None,
    usage: Usage | None = None,
    deadline_at: datetime | None = None,
    attempt: int = 1,
) -> NodeContext:
    return NodeContext(
        node=node,
        attempt=attempt,
        deps=deps,
        run_id="run-1",
        usage=usage or Usage(),
        budget=budget or ModeBudget.simple(),
        deadline_at=deadline_at,
    )
