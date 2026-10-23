"""POST /v1/research/{run_id}/approve (T020): decisions on a paused graph run."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import Engine, select, update

from agent.budget import ModeBudget
from agent.checkpoint import graph_serde
from agent.graph.build import build_graph
from agent.graph.context import GraphDeps, NodeContext, NodeFn
from agent.graph.nodes.decision import decision_node
from agent.graph.state import ResearchState
from agent.loop import Budget
from agent.plan import Plan
from agent.report import Report
from agent.runs import RunService
from api import schema
from api.auth.session import mint_test_session
from api.guards import Guards
from api.guards.quota import deep_quota_key, quota_key
from api.main import create_app
from tests.api_fakes import FakeRedis, fake_guards
from tests.integration.agent.helpers import FakeTools, add_user, redis_client
from tests.integration.api.test_research import ALLOWED, SETTINGS, parse_sse
from tests.unit.tools.test_documents import _registry
from tools.registry import GraphNode

QUESTION = "THYAO ve PGSUS net borç / FAVÖK karşılaştır"
STEP = {
    "id": "s1",
    "kind": "facts",
    "goal": "Net borç ve FAVÖK, son dört çeyrek",
    "tickers": ["THYAO", "PGSUS"],
    "periods": ["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"],
    "metrics": ["ebitda", "total_debt"],
}
PLAN = Plan.model_validate({"steps": [STEP], "estimate": {"typical": 6, "max": 15}})


@pytest.fixture
async def redis() -> AsyncIterator[Any]:
    async for client in redis_client():
        yield client


def _node(update: dict[str, Any] | None = None, seen: list[str] | None = None) -> NodeFn:
    async def run(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
        if seen is not None:
            seen.append(ctx.node)
        return dict(update or {})

    return run


class Setup:
    def __init__(self, engine: Engine, redis: Any, quota_redis: FakeRedis, offer: bool = False):
        self.visited: list[str] = []
        self.guards: Guards = fake_guards(ALLOWED, redis=quota_redis, deep_limit=1)
        self.service = RunService(engine, redis, None, FakeTools(), Budget(), agent_engine="graph")  # type: ignore[arg-type]
        registry, _ = _registry([])
        report = Report(language="tr", status="completed", summary="Tamam.")
        nodes: dict[GraphNode, NodeFn] = {
            "router": _node({"mode": "deep", "language": "tr", "budget": ModeBudget.deep()}),
            "quota_gate": _node({"decision_kind": "deep_quota_offer" if offer else None}),
            "planner": _node({"plan": PLAN}),
            "decision": decision_node,
            "retriever": _node(seen=self.visited),
            "analyst": _node(seen=self.visited),
            "writer": _node({"draft": report}, self.visited),
            "retriever_simple": _node(seen=self.visited),
            "writer_simple": _node({"draft": report}, self.visited),
        }
        deps = GraphDeps(
            router=None,  # type: ignore[arg-type]
            tools=registry,
            emit=self.service.emit_event,
            attempts=self.service.attempt,
            quota=self.guards.quota,
            runs=self.service,
        )
        self.service.attach_graph(build_graph(deps, InMemorySaver(serde=graph_serde()), nodes))

    def client(self, user: uuid.UUID, email: str = "user@example.test") -> httpx.AsyncClient:
        app = create_app(SETTINGS, run_service=self.service, guards=self.guards)
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            cookies=mint_test_session(user, SETTINGS, email),
        )

    async def start(self, client: httpx.AsyncClient) -> str:
        response = await client.post("/v1/research", json={"question": QUESTION})
        assert response.status_code == 202
        await self.service.wait_idle()
        return str(response.json()["run_id"])


def _row(engine: Engine, run_id: str) -> Any:
    with engine.connect() as conn:
        return conn.execute(select(schema.runs).where(schema.runs.c.id == uuid.UUID(run_id))).one()


async def test_approve_resumes_and_swaps_the_quota(engine: Engine, redis: Any) -> None:
    quota_redis = FakeRedis()
    setup = Setup(engine, redis, quota_redis)
    user = add_user(engine)
    async with setup.client(user) as client:
        run_id = await setup.start(client)
        row = _row(engine, run_id)
        assert (row.status, row.mode) == ("awaiting_approval", "deep")
        assert row.plan["steps"][0]["id"] == "s1"
        assert row.plan_expires_at > datetime.now(UTC) + timedelta(hours=23)

        paused = parse_sse((await client.get(f"/v1/research/{run_id}/stream")).text)
        assert paused[-1]["event"] == "awaiting_decision"
        assert paused[-1]["data"]["kind"] == "plan"
        assert paused[-1]["data"]["deep_remaining"] == 1
        detail = (await client.get(f"/v1/research/{run_id}")).json()
        assert (detail["status"], detail["engine"], detail["mode"]) == (
            "awaiting_approval",
            "graph",
            "deep",
        )
        assert detail["plan"]["steps"][0]["goal"] == STEP["goal"]

        response = await client.post(f"/v1/research/{run_id}/approve", json={"action": "approve"})
        assert response.status_code == 202
        body = response.json()
        assert (body["run_id"], body["status"]) == (run_id, "running")
        assert body["quota"]["deep"]["used"] == 1
        assert body["quota"]["simple"]["used"] == 0  # refunded by the swap
        await setup.service.wait_idle()

        rest = parse_sse(
            (
                await client.get(
                    f"/v1/research/{run_id}/stream",
                    headers={"Last-Event-ID": paused[-1]["id"]},
                )
            ).text
        )
        assert [e["event"] for e in rest] == ["decision_made", "final"]
        # Read from the start, a decided run's stream does not stop at the old pause.
        full = parse_sse((await client.get(f"/v1/research/{run_id}/stream")).text)
        assert full[-1]["event"] == "final"

    row = _row(engine, run_id)
    assert (row.status, row.decision) == ("completed", "approve")
    assert row.decided_at is not None
    assert setup.visited == ["retriever", "analyst", "writer"]


async def test_edit_replaces_the_plan(engine: Engine, redis: Any) -> None:
    setup = Setup(engine, redis, FakeRedis())
    user = add_user(engine)
    edited = {"steps": [{**STEP, "id": "s5", "goal": "Yalnızca THYAO", "tickers": ["THYAO"]}]}
    async with setup.client(user) as client:
        run_id = await setup.start(client)
        response = await client.post(
            f"/v1/research/{run_id}/approve", json={"action": "edit", "plan": edited}
        )
        assert response.status_code == 202
        await setup.service.wait_idle()

    row = _row(engine, run_id)
    assert row.decision == "edit"
    assert row.plan["steps"][0] | {"status": "pending"} == {
        **STEP,
        "id": "s1",
        "goal": "Yalnızca THYAO",
        "tickers": ["THYAO"],
        "status": "pending",
    }
    assert row.plan["edited"] is True
    assert row.status == "completed"
    values = await setup.service.graph_values(uuid.UUID(run_id))
    assert values["plan"].steps[0].goal == "Yalnızca THYAO"


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        ({"steps": [{**STEP, "goal": "THYAO almalı mıyım?"}]}, [("s1", "goal")]),
        ({"steps": [{**STEP, "periods": ["2025-Q7"]}]}, [("s1", "periods")]),
        ({"steps": [{**STEP, "id": f"s{i}"} for i in range(1, 10)]}, [(None, "steps")]),
        (None, [(None, "steps")]),
    ],
)
async def test_invalid_edit_leaves_the_run_waiting(
    engine: Engine, redis: Any, plan: Any, expected: list[tuple[str | None, str]]
) -> None:
    quota_redis = FakeRedis()
    setup = Setup(engine, redis, quota_redis)
    user = add_user(engine)
    async with setup.client(user) as client:
        run_id = await setup.start(client)
        response = await client.post(
            f"/v1/research/{run_id}/approve",
            json={"action": "edit", "plan": plan},
            headers={"Accept-Language": "en"},
        )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "plan_invalid"
    assert [(r["step"], r["field"]) for r in error["details"]["reasons"]] == expected
    assert _row(engine, run_id).status == "awaiting_approval"
    assert deep_quota_key(user, datetime.now(UTC)) not in quota_redis.values


async def test_cancel(engine: Engine, redis: Any) -> None:
    setup = Setup(engine, redis, FakeRedis())
    user = add_user(engine)
    async with setup.client(user) as client:
        run_id = await setup.start(client)
        response = await client.post(f"/v1/research/{run_id}/approve", json={"action": "cancel"})
        assert response.status_code == 202
        assert response.json()["status"] == "cancelled"
        assert response.json()["quota"]["simple"]["used"] == 1  # a cancelled plan keeps its unit
        await setup.service.wait_idle()
        events = parse_sse((await client.get(f"/v1/research/{run_id}/stream")).text)
        assert events[-1]["event"] == "run_cancelled"
        again = await client.post(f"/v1/research/{run_id}/approve", json={"action": "approve"})
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "already_decided"
    row = _row(engine, run_id)
    assert (row.status, row.decision) == ("cancelled", "cancel")
    assert row.finished_at is not None
    assert setup.visited == []


async def test_two_concurrent_approvals(engine: Engine, redis: Any) -> None:
    quota_redis = FakeRedis()
    setup = Setup(engine, redis, quota_redis)
    user = add_user(engine)
    async with setup.client(user) as first, setup.client(user) as second:
        run_id = await setup.start(first)
        responses = await asyncio.gather(
            first.post(f"/v1/research/{run_id}/approve", json={"action": "approve"}),
            second.post(f"/v1/research/{run_id}/approve", json={"action": "approve"}),
        )
        await setup.service.wait_idle()
    assert sorted(r.status_code for r in responses) == [202, 409]
    loser = next(r for r in responses if r.status_code == 409)
    assert loser.json()["error"]["code"] == "already_decided"
    assert quota_redis.values[deep_quota_key(user, datetime.now(UTC))] == "1"
    assert setup.visited.count("writer") == 1


async def test_other_user_gets_404(engine: Engine, redis: Any) -> None:
    setup = Setup(engine, redis, FakeRedis())
    owner, other = add_user(engine, "a@example.test"), add_user(engine, "b@example.test")
    async with setup.client(owner, "a@example.test") as client:
        run_id = await setup.start(client)
    async with setup.client(other, "b@example.test") as client:
        response = await client.post(f"/v1/research/{run_id}/approve", json={"action": "approve"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert _row(engine, run_id).status == "awaiting_approval"


async def test_expired_plan(engine: Engine, redis: Any) -> None:
    setup = Setup(engine, redis, FakeRedis())
    user = add_user(engine)
    async with setup.client(user) as client:
        run_id = await setup.start(client)
        with engine.begin() as conn:
            conn.execute(
                update(schema.runs)
                .where(schema.runs.c.id == uuid.UUID(run_id))
                .values(plan_expires_at=datetime.now(UTC) - timedelta(minutes=1))
            )
        response = await client.post(f"/v1/research/{run_id}/approve", json={"action": "approve"})
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "plan_expired"


async def test_deep_quota_used_up_between_plan_and_approval(engine: Engine, redis: Any) -> None:
    quota_redis = FakeRedis()
    setup = Setup(engine, redis, quota_redis)
    user = add_user(engine)
    async with setup.client(user) as client:
        run_id = await setup.start(client)
        # Another tab used the last deep unit while the plan waited.
        quota_redis.values[deep_quota_key(user, datetime.now(UTC))] = "1"
        response = await client.post(f"/v1/research/{run_id}/approve", json={"action": "approve"})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "quota_exhausted"
    row = _row(engine, run_id)
    assert (row.status, row.decided_at, row.decision) == ("awaiting_approval", None, None)
    assert quota_redis.values[quota_key(user, datetime.now(UTC))] == "1"
    assert setup.visited == []


async def test_action_must_fit_the_pending_kind(engine: Engine, redis: Any) -> None:
    setup = Setup(engine, redis, FakeRedis(), offer=True)
    user = add_user(engine)
    async with setup.client(user) as client:
        run_id = await setup.start(client)
        events = parse_sse((await client.get(f"/v1/research/{run_id}/stream")).text)
        assert events[-1]["data"]["kind"] == "deep_quota_offer"
        wrong = await client.post(f"/v1/research/{run_id}/approve", json={"action": "approve"})
        assert wrong.status_code == 409
        assert wrong.json()["error"]["code"] == "not_awaiting_decision"
        simple = await client.post(f"/v1/research/{run_id}/approve", json={"action": "simple"})
        assert simple.status_code == 202
        await setup.service.wait_idle()
    assert setup.visited == ["retriever_simple", "writer_simple"]
    assert _row(engine, run_id).status == "completed"


async def test_not_waiting_run(engine: Engine, redis: Any) -> None:
    setup = Setup(engine, redis, FakeRedis())
    user = add_user(engine)
    async with setup.client(user) as client:
        run_id = await setup.start(client)
        with engine.begin() as conn:
            conn.execute(
                update(schema.runs)
                .where(schema.runs.c.id == uuid.UUID(run_id))
                .values(status="running")
            )
        response = await client.post(f"/v1/research/{run_id}/approve", json={"action": "approve"})
        missing = await client.post(
            f"/v1/research/{uuid.uuid4()}/approve", json={"action": "approve"}
        )
        bad = await client.post(f"/v1/research/{run_id}/approve", json={"action": "maybe"})
    assert response.json()["error"]["code"] == "not_awaiting_decision"
    assert missing.status_code == 404
    assert bad.status_code == 422


async def test_stored_cancelled_and_expired_runs_replay(engine: Engine, redis: Any) -> None:
    setup = Setup(engine, redis, FakeRedis())
    user = add_user(engine)
    async with setup.client(user) as client:
        run_id = await setup.start(client)
        for status, event in (("cancelled", "run_cancelled"), ("expired", "run_expired")):
            with engine.begin() as conn:
                conn.execute(
                    update(schema.runs)
                    .where(schema.runs.c.id == uuid.UUID(run_id))
                    .values(status=status)
                )
            await redis.delete(f"run:{run_id}:events")
            events = parse_sse((await client.get(f"/v1/research/{run_id}/stream")).text)
            assert [e["event"] for e in events] == [event]
