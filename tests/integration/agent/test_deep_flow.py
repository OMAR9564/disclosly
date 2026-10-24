"""The deep path end to end (T023): real nodes, Postgres checkpoints, cassette replay.

The first pass records the model's replies by node into cassette entries; the second pass
replays them with `FakeRouter`, so every request a node builds must be byte-for-byte the same
on a re-run (the property the CI eval gate relies on).
"""

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import Engine

from agent.checkpoint import open_checkpointer, setup_checkpointer
from agent.graph.build import build_graph
from agent.graph.context import GraphDeps
from agent.graph.nodes.decision import Decision
from agent.loop import Budget
from agent.runs import RunService
from llm.testing import FakeRouter
from tests.graph_fixtures import FACTS, QUESTION, Recorder
from tests.integration.agent.helpers import FakeTools, add_user, redis_client
from tests.unit.agent.graph.test_retriever import Toolbox


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


def _service(engine: Engine, redis: Any, saver: AsyncPostgresSaver, router: Any) -> RunService:
    service = RunService(engine, redis, router, FakeTools(), Budget(), agent_engine="graph")
    toolbox = Toolbox({"THYAO"}, facts={"THYAO": FACTS})
    deps = GraphDeps(
        router=router,
        tools=toolbox,  # type: ignore[arg-type]
        emit=service.emit_event,
        attempts=service.attempt,
        runs=service,
    )
    service.attach_graph(build_graph(deps, saver))
    return service


async def _deep_run(
    engine: Engine, redis: Any, saver: AsyncPostgresSaver, router: Any, action: str
) -> tuple[RunService, uuid.UUID, uuid.UUID]:
    user = add_user(engine, f"{uuid.uuid4().hex[:8]}@example.test")
    service = _service(engine, redis, saver, router)
    run_id = await service.create(user, QUESTION)
    await service.wait_idle()
    now = datetime.now(UTC)
    status = await service.decide(run_id, user, action, now)
    assert status == ("cancelled" if action == "cancel" else "running")
    decision = Decision.model_validate({"action": action, "decided_at": now})
    service.resume(run_id, user, "tr", decision.model_dump(mode="json"))
    await service.wait_idle()
    return service, run_id, user


async def _events(service: RunService, run_id: uuid.UUID) -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    cursor: str | None = None
    while True:
        batch = [e async for e in service.events(run_id, cursor, block_ms=100) if e is not None]
        if not batch:
            return found
        found += [(e.event, e.data) for e in batch]
        cursor = batch[-1].id
        if batch[-1].event in ("final", "error", "run_cancelled"):
            return found


async def test_approved_plan_ends_with_a_report_replayed_from_cassettes(
    engine: Engine, redis: Any, saver: AsyncPostgresSaver
) -> None:
    recorder = Recorder()
    _, recorded_run, _ = await _deep_run(engine, redis, saver, recorder, "approve")
    assert [i["task_class"] for i in recorder.interactions] == [
        "cheap",  # router
        "strong",  # planner
        "cheap",  # retriever sufficiency
        "cheap",  # analyst (no calculate step)
        "strong",  # verifier (claim support)
        "strong",  # writer
    ]

    replay = FakeRouter().load_interactions(recorder.interactions)
    service, run_id, user = await _deep_run(engine, redis, saver, replay, "approve")

    assert len(replay.calls) == 6
    run = await service.get(run_id, user)
    assert run is not None
    assert (run["status"], run["mode"], run["engine"], run["decision"]) == (
        "completed",
        "deep",
        "graph",
        "approve",
    )
    report = run["answer"]
    assert report["chart"] == {
        "kind": "line",
        "table": 0,
        "x_column": "Dönem",
        "series": ["Toplam borç"],
    }
    assert [row[1]["cite_id"] for row in report["tables"][0]["rows"]] == [
        "f:80",
        "f:81",
        "f:82",
        "f:83",
    ]
    assert report["confidence"]["level"] == "high"
    assert report["disclaimer"] == "Yatırım tavsiyesi değildir / Not investment advice"
    assert {c["cite_id"] for c in run["citations"]} == {"f:80", "f:81", "f:82", "f:83"}
    assert run["usage"]["model_calls"] == 6
    assert set(run["usage"]["nodes"]) >= {
        "router",
        "planner",
        "retriever",
        "analyst",
        "verifier",
        "writer",
    }

    events = await _events(service, run_id)
    names = [e for e, _ in events]
    assert names.index("plan_proposed") < names.index("awaiting_decision")
    assert names.index("awaiting_decision") < names.index("decision_made")
    assert names[-1] == "final"
    assert all("node" in data and "attempt" in data for _, data in events)
    steps = [d for e, d in events if e == "step_finished"]
    assert steps[0]["status"] == "done"
    assert recorded_run != run_id


async def test_cancel_makes_no_model_call_after_the_plan(
    engine: Engine, redis: Any, saver: AsyncPostgresSaver
) -> None:
    replay = FakeRouter().load_interactions(await _recorded(engine, redis, saver))
    service, run_id, user = await _deep_run(engine, redis, saver, replay, "cancel")

    assert [task for task, _ in replay.calls] == ["cheap", "strong"]  # router and planner only
    run = await service.get(run_id, user)
    assert run is not None
    assert (run["status"], run["decision"]) == ("cancelled", "cancel")
    events = await _events(service, run_id)
    assert [e for e, _ in events][-2:] == ["decision_made", "run_cancelled"]


async def _recorded(engine: Engine, redis: Any, saver: AsyncPostgresSaver) -> list[dict[str, Any]]:
    recorder = Recorder()
    await _deep_run(engine, redis, saver, recorder, "approve")
    return recorder.interactions
