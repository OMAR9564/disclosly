"""agent.graph.state (T010): value objects, reducers and checkpoint round trips."""

import uuid
import warnings
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ValidationError

from agent.budget import ModeBudget, Usage
from agent.checkpoint import graph_serde
from agent.graph.state import (
    Calculation,
    CalculationInput,
    Finding,
    MemoryIntent,
    MemorySnapshot,
    ResearchState,
    RouterDecision,
    VerificationFailure,
    VerificationResult,
    add_evidence,
    evidence_by_id,
)
from agent.plan import Estimate, Plan
from agent.report import Report
from tools.types import SeenItem

FACT = SeenItem(
    cite_id="f:1", text="Net kâr: 1.234,5 milyon TRY", value=Decimal("1234.5"), source={"k": 1}
)
CHUNK = SeenItem(cite_id="c:abc", text="Faaliyet raporu metni", source={"kind": "chunk"})


def _calc(**fields: Any) -> dict[str, Any]:
    calc: dict[str, Any] = {
        "cite_id": "k:1",
        "name": "net_debt_to_ebitda",
        "description": "Net borç / FAVÖK",
        "inputs": [{"name": "net_debt", "cite_id": "f:88", "value": "41200000000"}],
        "code": "result = {'net_debt_to_ebitda': inputs['net_debt'] / inputs['ebitda']}",
        "output": "1.9",
    }
    calc.update(fields)
    return calc


def _state() -> ResearchState:
    return {
        "run_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "question": "THYAO ve PGSUS net borç / FAVÖK karşılaştır",
        "language": "tr",
        "mode": "deep",
        "refused": False,
        "router": RouterDecision(
            mode="deep",
            tickers=["thyao", "PGSUS"],
            periods=["2025-Q4"],
            memory_intent=MemoryIntent(action="watchlist_add", ticker="thyao"),
        ),
        "memory": MemorySnapshot(watchlist=["THYAO"], default_period_range="last_8q"),
        "plan": Plan.model_validate(
            {
                "steps": [{"id": "s1", "kind": "facts", "goal": "Net borç", "tickers": ["THYAO"]}],
                "estimate": {"typical": 6, "max": 15},
            }
        ),
        "plan_approved": True,
        "decision_kind": None,
        "cancelled": False,
        "evidence": [FACT, CHUNK],
        "findings": [Finding(id="F1", text="Net kâr arttı", cite_ids=["f:1"])],
        "calculations": [Calculation.model_validate(_calc())],
        "verification": VerificationResult(
            round=1,
            passed=False,
            failures=[VerificationFailure(kind="recompute_mismatch", ref="k:1", reason="x")],
        ),
        "verify_rounds": 1,
        "draft": Report(language="tr", status="completed", summary="Özet"),
        "budget": ModeBudget.deep(),
        "deadline_at": datetime(2026, 10, 22, 9, 5, tzinfo=UTC),
        "usage": Usage(model_calls=3, providers=["groq"]),
        "limit_hit": None,
        "errors": ["sandbox_unavailable"],
    }


def test_router_decision_normalises_tickers() -> None:
    decision = RouterDecision(mode="simple", tickers=["thyao", " THYAO ", "aapl", ""])
    assert decision.tickers == ["THYAO", "AAPL"]
    assert decision.uses_watchlist is False
    assert MemoryIntent(action="watchlist_remove", ticker=" aapl").ticker == "AAPL"


@pytest.mark.parametrize(
    "fields",
    [
        {"cite_id": "c:1"},
        {"cite_id": "k:0"},
        {"name": "NetDebt"},
        {"name": "1ratio"},
        {"name": "x" * 41},
        {"code": "x" * 4001},
        {"inputs": [{"name": "Bad Name", "cite_id": "f:1", "value": "1"}]},
    ],
)
def test_calculation_limits(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Calculation.model_validate(_calc(**fields))


def test_finding_needs_a_citation() -> None:
    with pytest.raises(ValidationError):
        Finding(id="F1", text="claim", cite_ids=[])


def test_memory_snapshot_rejects_unknown_range() -> None:
    with pytest.raises(ValidationError):
        MemorySnapshot.model_validate({"default_period_range": "last_5y"})


def test_add_evidence_dedups_by_cite_id() -> None:
    other = SeenItem(cite_id="f:1", text="different text", source={})
    merged = add_evidence([FACT], [other, CHUNK, CHUNK])
    assert merged == [FACT, CHUNK]
    assert add_evidence(None, [FACT]) == [FACT]
    assert add_evidence([FACT], None) == [FACT]
    assert evidence_by_id(merged) == {"f:1": FACT, "c:abc": CHUNK}


def test_state_round_trips_through_the_checkpoint_serializer() -> None:
    serde = graph_serde()
    state = _state()
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # an unregistered type would warn
        restored = serde.loads_typed(serde.dumps_typed(state))
    assert restored == state
    calc = restored["calculations"][0]
    assert isinstance(calc, Calculation)
    assert calc.inputs[0] == CalculationInput(
        name="net_debt", cite_id="f:88", value=Decimal("41200000000")
    )
    assert isinstance(restored["plan"], Plan)
    assert restored["plan"].estimate == Estimate(typical=6, max=15)


class _Unregistered(BaseModel):
    payload: str


def test_serializer_does_not_rebuild_unknown_classes() -> None:
    serde = graph_serde()
    restored = serde.loads_typed(serde.dumps_typed({"x": _Unregistered(payload="p")}))
    assert not isinstance(restored["x"], _Unregistered)


async def test_reducers_in_a_checkpointed_graph() -> None:
    async def first(state: ResearchState) -> dict[str, Any]:
        return {"evidence": [FACT], "calculations": [Calculation.model_validate(_calc())]}

    async def second(state: ResearchState) -> dict[str, Any]:
        calc = Calculation.model_validate(_calc(cite_id="k:2"))
        return {"evidence": [FACT, CHUNK], "calculations": [calc], "errors": ["e1"]}

    builder = StateGraph(ResearchState)
    builder.add_node("first", first)
    builder.add_node("second", second)
    builder.add_edge(START, "first")
    builder.add_edge("first", "second")
    builder.add_edge("second", END)
    graph = builder.compile(checkpointer=InMemorySaver(serde=graph_serde()))
    config: Any = {"configurable": {"thread_id": "t1"}}

    await graph.ainvoke({"question": "q", "errors": []}, config, durability="sync")
    values = (await graph.aget_state(config)).values

    assert [item.cite_id for item in values["evidence"]] == ["f:1", "c:abc"]
    assert [c.cite_id for c in values["calculations"]] == ["k:1", "k:2"]
    assert values["errors"] == ["e1"]
