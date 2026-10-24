"""The verification loop end to end (T036): real nodes, Postgres checkpoints, a local sandbox.

A failed verification sends the run back to the planner once; passing on the second round
gives a completed report, failing again gives a partial one listing what was dropped.
"""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import Engine

from agent.graph.build import build_graph
from agent.graph.context import GraphDeps
from agent.graph.nodes.decision import Decision
from agent.loop import Budget
from agent.runs import RunService
from llm.types import ChatRequest, ChatResponse, TaskClass
from tests.graph_fixtures import FACTS, PROMPT_MARKERS, QUESTION, REPLIES
from tests.integration.agent.helpers import FakeTools, add_user, redis_client
from tests.integration.agent.test_deep_flow import _events, saver  # noqa: F401  (fixture)
from tests.unit.agent.graph.test_analyst_calcs import LocalSandbox
from tests.unit.agent.graph.test_retriever import Toolbox

GROWTH = {
    "name": "debt_growth",
    "description": "Toplam borç 2025-Q4 / 2025-Q1 - 1",
    "inputs": [
        {"name": "q1", "cite_id": "f:80"},
        {"name": "q4", "cite_id": "f:83"},
    ],
    "code": "result = inputs['q4'] / inputs['q1'] - 1",
}


def _claims(failing: set[str]) -> dict[str, Any]:
    return {
        "claims": [
            {"finding_id": f"F{i}", "supported": f"F{i}" not in failing, "reason": "x"}
            for i in range(1, 5)
        ]
    }


class NodeScript:
    """Answers each node from its own queue; the last reply repeats."""

    def __init__(self, verifier: list[dict[str, Any]]) -> None:
        analyst = {**REPLIES["analyst"], "calculations": [GROWTH]}
        self.replies: dict[str, list[dict[str, Any]]] = {
            **{node: [reply] for node, reply in REPLIES.items()},
            "analyst": [analyst],
            "verifier": verifier,
        }
        self.calls: list[str] = []

    async def chat(self, request: ChatRequest, *, task_class: TaskClass) -> ChatResponse:
        system = request.messages[0].content or ""
        node = next(n for n, marker in PROMPT_MARKERS.items() if marker in system)
        self.calls.append(node)
        queue = self.replies[node]
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        return ChatResponse(
            content=json.dumps(reply, ensure_ascii=False), provider="gemini", model="m"
        )


@pytest.fixture
async def redis() -> AsyncIterator[Any]:
    async for client in redis_client():
        yield client


async def _run(
    engine: Engine, redis: Any, checkpoints: AsyncPostgresSaver, router: NodeScript
) -> tuple[RunService, uuid.UUID, uuid.UUID, LocalSandbox]:
    service = RunService(engine, redis, router, FakeTools(), Budget(), agent_engine="graph")
    sandbox = LocalSandbox()
    deps = GraphDeps(
        router=router,
        tools=Toolbox({"THYAO"}, facts={"THYAO": FACTS}),  # type: ignore[arg-type]
        emit=service.emit_event,
        attempts=service.attempt,
        runs=service,
        sandbox=sandbox,
    )
    service.attach_graph(build_graph(deps, checkpoints))
    user = add_user(engine, f"{uuid.uuid4().hex[:8]}@example.test")
    run_id = await service.create(user, QUESTION)
    await service.wait_idle()
    now = datetime.now(UTC)
    assert await service.decide(run_id, user, "approve", now) == "running"
    decision = Decision.model_validate({"action": "approve", "decided_at": now})
    service.resume(run_id, user, "tr", decision.model_dump(mode="json"))
    await service.wait_idle()
    return service, run_id, user, sandbox


async def test_passing_on_the_second_round_completes(
    engine: Engine,
    redis: Any,
    saver: AsyncPostgresSaver,  # noqa: F811
) -> None:
    router = NodeScript([_claims({"F1"}), _claims(set())])
    service, run_id, user, sandbox = await _run(engine, redis, saver, router)

    assert router.calls == [
        "router",
        "planner",
        "retriever",
        "analyst",
        "verifier",
        "planner",  # revision within the approved scope
        "retriever",
        "analyst",
        "verifier",
        "writer",
    ]
    # Analyst and verifier each run the round's calculations once.
    assert len(sandbox.runs) == 4
    run = await service.get(run_id, user)
    assert run is not None
    assert run["status"] == "completed"
    assert run["answer"]["unverified"] == []
    assert run["usage"]["sandbox_runs"] == 4
    events = await _events(service, run_id)
    rounds = [d for e, d in events if e == "verification"]
    assert [(d["round"], d["passed"], d["next"]) for d in rounds] == [
        (1, False, "planner"),
        (2, True, "writer"),
    ]
    revisions = [d["revision"] for e, d in events if e == "plan_proposed"]
    assert revisions == [0, 1]
    calcs = [d for e, d in events if e == "calculation"]
    assert [(d["cite_id"], d["status"]) for d in calcs] == [("k:1", "ok"), ("k:2", "ok")]


async def test_failing_twice_gives_a_partial_report_with_the_dropped_finding(
    engine: Engine,
    redis: Any,
    saver: AsyncPostgresSaver,  # noqa: F811
) -> None:
    router = NodeScript([_claims({"F1"})])
    service, run_id, user, _ = await _run(engine, redis, saver, router)

    assert router.calls.count("verifier") == 2
    assert router.calls[-1] == "writer"
    run = await service.get(run_id, user)
    assert run is not None
    report = run["answer"]
    assert run["status"] == "partial"
    assert report["status"] == "partial"
    assert report["confidence"]["level"] == "low"
    first_finding = REPLIES["analyst"]["findings"][0]["text"]
    assert report["unverified"] == [first_finding]
    events = await _events(service, run_id)
    rounds = [d for e, d in events if e == "verification"]
    assert [(d["round"], d["passed"], d["next"]) for d in rounds] == [
        (1, False, "planner"),
        (2, False, "writer"),
    ]
