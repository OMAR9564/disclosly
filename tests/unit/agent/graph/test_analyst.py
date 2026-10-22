"""analyst node (T022): cited findings from the gathered evidence."""

import json
from decimal import Decimal
from typing import Any

from agent.budget import ModeBudget, Usage
from agent.graph.nodes.analyst import analyst_node, evidence_block, task_class
from agent.graph.state import ResearchState
from agent.plan import Plan
from llm.types import ChatResponse
from tests.agent_fakes import POISON, ScriptedRouter
from tests.unit.agent.graph.helpers import make_ctx, make_deps
from tools.types import SeenItem

FACT = SeenItem(
    cite_id="f:88",
    text="Net borç (2025-Q4): 41,2 milyar TRY",
    value=Decimal("41.2"),
    source={"kind": "fact"},
)
PLAN = Plan.model_validate(
    {"steps": [{"id": "s1", "kind": "facts", "goal": "Net borç", "tickers": ["THYAO"]}]}
)


def _findings(*findings: dict[str, Any]) -> ChatResponse:
    return ChatResponse(
        content=json.dumps({"findings": list(findings)}, ensure_ascii=False),
        provider="gemini",
        model="m",
    )


def _state(**fields: Any) -> ResearchState:
    state: ResearchState = {
        "question": "THYAO net borcu ne kadar?",
        "language": "tr",
        "plan": PLAN,
        "evidence": [FACT, POISON],
        "budget": ModeBudget.deep(),
        "usage": Usage(model_calls=3),
    }
    state.update(fields)  # type: ignore[typeddict-item]
    return state


async def _run(router: ScriptedRouter, **fields: Any) -> dict[str, Any]:
    ctx = make_ctx(
        make_deps(router), "analyst", budget=ModeBudget.deep(), usage=Usage(model_calls=3)
    )
    return await analyst_node(_state(**fields), ctx)


async def test_findings_cite_the_evidence() -> None:
    router = ScriptedRouter(
        [_findings({"id": "F1", "text": "Net borç 41,2 milyar TL", "cite_ids": ["f:88"]})]
    )

    update = await _run(router)

    assert [f.id for f in update["findings"]] == ["F1"]
    task, request = router.calls[0]
    assert task == "cheap"
    user = request.messages[1].content or ""
    assert '<untrusted_document cite_id="f:88">' in user
    # A poisoned chunk cannot close its own wrapper.
    assert user.count("</untrusted_document>") == 2
    assert '"plan"' in user


async def test_findings_with_unknown_ids_are_dropped() -> None:
    router = ScriptedRouter(
        [
            _findings(
                {"id": "F1", "text": "Net borç 41,2 milyar TL", "cite_ids": ["f:88"]},
                {"id": "F2", "text": "Hedef fiyat 500 TL", "cite_ids": ["f:999"]},
                {"id": "F3", "text": "Karışık", "cite_ids": ["f:88", "c:zzz"]},
            )
        ]
    )
    update = await _run(router)
    assert [f.id for f in update["findings"]] == ["F1"]


async def test_invalid_reply_is_reasked_once() -> None:
    router = ScriptedRouter(
        [
            _findings({"id": "F1", "text": "Net borç", "cite_ids": []}),
            _findings({"id": "F1", "text": "Net borç 41,2 milyar TL", "cite_ids": ["f:88"]}),
        ]
    )
    update = await _run(router)
    assert len(router.calls) == 2
    assert "cite_ids" in (router.calls[1][1].messages[-1].content or "")
    assert len(update["findings"]) == 1


async def test_two_invalid_replies_leave_no_findings() -> None:
    bad = ChatResponse(content="nope", provider="g", model="m")
    router = ScriptedRouter([bad, bad])
    assert await _run(router) == {"findings": [], "calculations": []}


def test_strong_when_the_plan_calculates() -> None:
    calc = Plan.model_validate(
        {
            "steps": [
                {"id": "s1", "kind": "facts", "goal": "Net borç"},
                {"id": "s2", "kind": "calculate", "goal": "Oran"},
            ]
        }
    )
    assert task_class(calc) == "strong"
    assert task_class(PLAN) == "cheap"
    assert task_class(None) == "cheap"


def test_evidence_block_limits() -> None:
    items = [SeenItem(cite_id=f"c:{i}", text="x" * 5000, source={}) for i in range(100)]
    block = evidence_block(items)
    assert block.count("<untrusted_document") == 60
    assert "x" * 1501 not in block
