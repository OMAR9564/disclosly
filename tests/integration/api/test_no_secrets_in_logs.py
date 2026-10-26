"""SC-008: sign-in and a full run leave no email, JWT, OAuth code or key in logs or spans;
a graph run leaves no memory value or calculation code or input either (T053)."""

import json
import logging
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import Engine

import agent.telemetry
import llm.telemetry
import tools.python
from agent import memory
from agent.graph.build import build_graph
from agent.graph.context import GraphDeps
from agent.graph.nodes.decision import Decision
from agent.loop import Budget
from agent.memory import EngineMemory
from agent.runs import RunService
from api.auth.session import REFRESH_COOKIE, SESSION_COOKIE
from api.main import create_app
from tests.api_fakes import FakeRedis, fake_guards
from tests.graph_fixtures import FACTS
from tests.graph_fixtures import QUESTION as GRAPH_QUESTION
from tests.integration.agent.helpers import FACT, FakeTools, add_user, good_script, redis_client
from tests.integration.agent.test_deep_flow import saver  # noqa: F401  (fixture)
from tests.integration.agent.test_verify_loop import GROWTH, NodeScript, _claims
from tests.integration.api.test_auth import SETTINGS, Providers, _cookies, _start
from tests.integration.api.test_research import QUESTION
from tests.integration.retrieval.corpus import Corpus, load
from tests.unit.agent.graph.test_analyst_calcs import LocalSandbox
from tests.unit.agent.graph.test_retriever import Toolbox
from tests.unit.llm.test_telemetry import RecordingTracer

OAUTH_CODE = "oauth-code-4f9b2c7e1d"
EMAIL = "ada@example.com"


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """`create_app` installs the production JSON handler on stdout; put the old ones back after."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        yield
    finally:
        root.handlers, root.level = handlers, level
        logging.getLogger("uvicorn.access").disabled = False


@pytest.fixture
async def stream_redis() -> AsyncIterator[Any]:
    async for client in redis_client():
        yield client


@pytest.fixture
def corpus_db(engine: Engine) -> Corpus:
    return load(engine)


@pytest.fixture
def tracer(monkeypatch: pytest.MonkeyPatch) -> RecordingTracer:
    recording = RecordingTracer()
    monkeypatch.setattr(agent.telemetry, "_tracer", recording)
    monkeypatch.setattr(llm.telemetry, "_tracer", recording)
    return recording


def _secrets() -> list[str]:
    values = [
        SETTINGS.jwt_secret,
        SETTINGS.google_client_secret,
        SETTINGS.github_client_secret,
        SETTINGS.turnstile_secret_key,
    ]
    return [v.get_secret_value() for v in values if v is not None]


async def test_sign_in_and_run_leave_no_secrets(
    engine: Engine,
    stream_redis: Any,
    capsys: pytest.CaptureFixture[str],
    restore_logging: None,
    tracer: RecordingTracer,
) -> None:
    redis = FakeRedis()
    providers = Providers()
    guards = fake_guards(
        {EMAIL},
        redis=redis,
        engine=engine,
        http=httpx.AsyncClient(transport=httpx.MockTransport(providers)),
    )
    service = RunService(
        engine,
        stream_redis,
        good_script(),
        FakeTools({"get_financial_facts": [FACT]}),
        Budget(),
    )
    app = create_app(SETTINGS, run_service=service, guards=guards)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://api.disclosly.com"
    ) as client:
        state = await _start(client, "google")
        callback = await client.get(
            "/v1/auth/google/callback", params={"code": OAUTH_CODE, "state": state}
        )
        assert callback.status_code == 302
        jar = _cookies(callback)
        session, refresh = jar[SESSION_COOKIE].value, jar[REFRESH_COOKIE].value
        client.cookies.set(SESSION_COOKIE, session)

        started = await client.post("/v1/research", json={"question": QUESTION})
        assert started.status_code == 202, started.text
        await service.wait_idle()
        run = await client.get(f"/v1/research/{started.json()['run_id']}")
        assert run.json()["status"] == "completed"
        # A failing request logs too: the validation handler must not echo the input.
        await client.post("/v1/research", json={"question": EMAIL[:2]})

    logs = capsys.readouterr().out
    assert logs, "the handler captured nothing"
    for line in logs.splitlines():
        json.loads(line)  # every line is one JSON record
    spans: list[dict[str, Any]] = [{"name": s.name, **s.attributes} for s in tracer.spans]
    assert spans, "no spans were recorded"
    captured = logs + json.dumps(spans, default=str)

    forbidden = {
        "email": EMAIL,
        "email (as given)": "Ada@Example.com",
        "session JWT": session,
        "refresh token": refresh,
        "OAuth code": OAUTH_CODE,
        "OAuth state": state,
        "provider access token": "provider-token",
        **{f"secret {i}": value for i, value in enumerate(_secrets())},
    }
    for label, value in forbidden.items():
        assert value not in captured, f"{label} leaked into logs or spans"


async def test_graph_run_with_memory_and_a_calculation_leaves_no_private_values(
    engine: Engine,
    corpus_db: Any,
    stream_redis: Any,
    saver: AsyncPostgresSaver,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
    tracer: RecordingTracer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T053: a deep run that reads the user's memory and runs a calculation logs and traces
    none of: the email, a key, a memory value, the calculation code or its inputs."""
    monkeypatch.setattr(tools.python, "_tracer", tracer)
    caplog.set_level(logging.DEBUG)
    user = add_user(engine, EMAIL)
    memory.add_to_watchlist(engine, user, "AAPL")
    memory.set_preferences(engine, user, "en", "last_3fy")

    router = NodeScript([_claims(set())])
    service = RunService(engine, stream_redis, router, FakeTools(), Budget(), agent_engine="graph")
    deps = GraphDeps(
        router=router,
        tools=Toolbox({"THYAO"}, facts={"THYAO": FACTS}),  # type: ignore[arg-type]
        emit=service.emit_event,
        attempts=service.attempt,
        runs=service,
        sandbox=LocalSandbox(),
        memory=EngineMemory(engine),
    )
    service.attach_graph(build_graph(deps, saver))
    run_id = await service.create(user, GRAPH_QUESTION)
    await service.wait_idle()
    now = datetime.now(UTC)
    assert await service.decide(run_id, user, "approve", now) == "running"
    decision = Decision.model_validate({"action": "approve", "decided_at": now})
    service.resume(run_id, user, "tr", decision.model_dump(mode="json"))
    await service.wait_idle()
    run = await service.get(run_id, user)
    assert run is not None and run["status"] == "completed"
    assert run["usage"]["sandbox_runs"] >= 1, "the calculation did not run"

    spans = [{"name": s.name, **(s.attributes or {})} for s in tracer.spans]
    assert {"agent.node", "sandbox.run", "agent.verify"} <= {s["name"] for s in spans}
    captured = "\n".join(r.getMessage() for r in caplog.records)
    captured += json.dumps([r.__dict__ for r in caplog.records], default=str)
    captured += json.dumps(spans, default=str)

    forbidden = {
        "email": EMAIL,
        "user id": str(user),
        "watchlist ticker": "AAPL",
        "answer language preference": "answer_language",
        "default period preference": "last_3fy",
        "calculation code": str(GROWTH["code"]),
        "calculation input (Q1)": "35100000000",
        "calculation input (Q4)": "41200000000",
        **{f"secret {i}": value for i, value in enumerate(_secrets())},
    }
    for label, value in forbidden.items():
        assert value not in captured, f"{label} leaked into logs or spans"
