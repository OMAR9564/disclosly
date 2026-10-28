"""Graph spans (T054, research §R17): one `agent.node` per node with its attempt,
`sandbox.run` per calculation batch, `agent.verify` per round, `agent.resume` after a restart.

Spans are collected in memory by a recording tracer (no exporter, no network)."""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

import agent.telemetry
import tools.python
from agent.runs import InterruptedRun, RunService
from agent.telemetry import NODE_SPAN, RESUME_SPAN, VERIFY_SPAN
from evals.graph_cases import (
    CASSETTES_DIR,
    cassette_path,
    load_graph_dataset,
    read_cassette,
    replay,
    replay_sandbox,
    run_graph_case,
)
from llm.telemetry import hash_user_id
from tests.unit.llm.test_telemetry import RecordingTracer
from tools.python import SANDBOX_SPAN

# A recorded deep run with two verification rounds and four calculation batches (it changes
# when the cassettes are re-recorded: pick a deep case whose replay still has both).
CASE_ID = "tr-deep-eregl-net-kar-marji"


@pytest.fixture
def tracer(monkeypatch: pytest.MonkeyPatch) -> RecordingTracer:
    recording = RecordingTracer()
    monkeypatch.setattr(agent.telemetry, "_tracer", recording)
    monkeypatch.setattr(tools.python, "_tracer", recording)
    return recording


def _named(tracer: RecordingTracer, name: str) -> list[dict[str, Any]]:
    return [dict(s.attributes or {}) for s in tracer.spans if s.name == name]


async def test_a_deep_run_traces_every_node_calculation_and_round(
    tracer: RecordingTracer,
) -> None:
    case = next(c for c in load_graph_dataset("deep_golden") if c.id == CASE_ID)
    cassette = read_cassette(cassette_path(CASE_ID, CASSETTES_DIR))
    router, toolbox = replay(cassette)
    outcome = await run_graph_case(case, router, toolbox, replay_sandbox(cassette))

    nodes = _named(tracer, NODE_SPAN)
    names = [n["disclosly.node"] for n in nodes]
    assert names[:2] == ["router", "quota_gate"]
    assert {"planner", "retriever", "analyst", "verifier", "writer"} <= set(names)
    assert all(n["disclosly.attempt"] == 1 for n in nodes)
    assert all(n["disclosly.run_id"] == CASE_ID for n in nodes)
    # The user id is hashed, never stored as given.
    assert {n["disclosly.user_id_hash"] for n in nodes} == {hash_user_id("eval")}
    assert all(n["disclosly.user_id_hash"] != "eval" for n in nodes)
    assert all("disclosly.model_calls" in n for n in nodes)

    sandbox = _named(tracer, SANDBOX_SPAN)
    assert len(sandbox) == len(cassette.sandbox)
    assert all(s["disclosly.sandbox.status"] in ("ok", "error") for s in sandbox)

    rounds = _named(tracer, VERIFY_SPAN)
    assert [r["disclosly.verify.round"] for r in rounds] == list(
        range(1, names.count("verifier") + 1)
    )
    assert len(rounds) == names.count("verifier") == 2
    first, last = rounds[0], rounds[-1]
    assert first["disclosly.verify.passed"] is False
    assert any(k.startswith("disclosly.verify.failures.") for k in first)
    verification = outcome.values["verification"]
    assert last["disclosly.verify.passed"] is verification.passed


class _Graph:
    async def aget_state(self, config: Any) -> Any:
        return SimpleNamespace(next=("analyst",), interrupts=())


async def test_a_resumed_run_records_where_it_resumed(tracer: RecordingTracer) -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(run_id: uuid.UUID, event: str, data: dict[str, Any]) -> None:
        events.append((event, data))

    async def drive(*args: Any) -> None:
        return None

    service: Any = SimpleNamespace(
        _graph=_Graph(), _emit_service=emit, _spawn_graph=lambda coro: coro.close(), _drive=drive
    )
    run = InterruptedRun(uuid.uuid4(), uuid.uuid4(), "tr", None, None, None)
    await RunService.resume_interrupted(service, run, lease=None, resume_count=2)  # type: ignore[arg-type]

    [span] = _named(tracer, RESUME_SPAN)
    assert span == {
        "disclosly.run_id": str(run.id),
        "disclosly.resume.from_node": "analyst",
        "disclosly.resume.count": 2,
    }
    assert str(run.user_id) not in str(span)
    assert events == [("run_resumed", {"from_node": "analyst", "resume_count": 2})]
