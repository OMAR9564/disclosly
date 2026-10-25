"""writer node (T023): Report from checked findings, code checks, re-ask, partial fallback."""

import json
from decimal import Decimal
from typing import Any

from agent.answer import DISCLAIMER
from agent.budget import ModeBudget, Usage
from agent.graph.nodes.writer import (
    calculation_material,
    display_number,
    verified_outputs,
    writer_node,
)
from agent.graph.state import Calculation, Finding, ResearchState
from agent.plan import Plan
from agent.report import Report, validate_report
from llm.types import ChatResponse
from tests.agent_fakes import ScriptedRouter
from tests.unit.agent.graph.helpers import make_ctx, make_deps
from tools.types import SeenItem

Q3 = SeenItem(
    cite_id="f:87",
    text="Net borç (2025-Q3): 38,5 milyar TRY",
    value=Decimal("38.5"),
    scale=Decimal(10) ** 9,
    source={"kind": "fact"},
)
Q4 = SeenItem(
    cite_id="f:88",
    text="Net borç (2025-Q4): 41,2 milyar TRY",
    value=Decimal("41.2"),
    scale=Decimal(10) ** 9,
    source={"kind": "fact"},
)
FINDINGS = [
    Finding(id="F1", text="Net borç 2025-Q3'te 38,5 milyar TL", cite_ids=["f:87"]),
    Finding(id="F2", text="Net borç 2025-Q4'te 41,2 milyar TL", cite_ids=["f:88"]),
]
PLAN = Plan.model_validate(
    {"steps": [{"id": "s1", "kind": "facts", "goal": "Net borç", "status": "done"}]}
)


def _report(**fields: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "language": "tr",
        "status": "completed",
        "summary": "THYAO'nun net borcu 2025-Q4'te 41,2 milyar TL oldu [1].",
        "points": [{"text": "Net borç 41,2 milyar TL", "cite_ids": ["f:88"]}],
        "tables": [
            {
                "title": "Net borç",
                "columns": ["Dönem", "Net borç"],
                "rows": [
                    [{"text": "2025-Q3"}, {"text": "38,5 milyar TL", "cite_id": "f:87"}],
                    [{"text": "2025-Q4"}, {"text": "41,2 milyar TL", "cite_id": "f:88"}],
                ],
            }
        ],
        "chart": {"kind": "line", "table": 0, "x_column": "Dönem", "series": ["Net borç"]},
        "confidence": {"level": "high", "note": "Her iki çeyrek de raporlanmış."},
        "citations": [{"marker": 1, "cite_id": "f:88"}],
    }
    report.update(fields)
    return report


def _reply(body: dict[str, Any] | str) -> ChatResponse:
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    return ChatResponse(content=text, provider="gemini", model="m")


def _state(**fields: Any) -> ResearchState:
    state: ResearchState = {
        "question": "THYAO net borcu son iki çeyrekte nasıl değişti?",
        "language": "tr",
        "plan": PLAN,
        "findings": FINDINGS,
        "evidence": [Q3, Q4],
        "calculations": [],
        "budget": ModeBudget.deep(),
        "usage": Usage(model_calls=5),
    }
    state.update(fields)  # type: ignore[typeddict-item]
    return state


async def _run(router: ScriptedRouter, **fields: Any) -> dict[str, Any]:
    ctx = make_ctx(
        make_deps(router), "writer", budget=ModeBudget.deep(), usage=Usage(model_calls=5)
    )
    return await writer_node(_state(**fields), ctx)


async def test_report_with_table_and_chart() -> None:
    router = ScriptedRouter([_reply(_report(disclaimer="buy"))])

    update = await _run(router)

    draft = update["draft"]
    assert draft.status == "completed"
    assert draft.chart is not None
    assert len(draft.tables[0].rows) == 2
    assert draft.disclaimer == DISCLAIMER
    task, request = router.calls[0]
    assert task == "strong"
    user = request.messages[1].content or ""
    assert '<untrusted_document cite_id="f:88">' in user
    assert '"findings"' in user


async def test_invalid_report_is_reasked() -> None:
    bad = _report()
    bad["tables"][0]["rows"][1][1]["text"] = "45,0 milyar TL"
    router = ScriptedRouter([_reply(bad), _reply(_report())])

    update = await _run(router)

    assert update["draft"].status == "completed"
    reask = router.calls[1][1].messages[-1].content or ""
    assert "numbers not found in the cited source" in reask


async def test_report_failing_twice_keeps_only_checked_parts() -> None:
    bad = _report()
    bad["tables"][0]["rows"][1][1]["text"] = "45,0 milyar TL"
    router = ScriptedRouter([_reply(bad), _reply(bad)])

    draft = (await _run(router))["draft"]

    assert draft.status == "partial"
    assert draft.confidence.level == "low"
    assert len(draft.tables[0].rows) == 1
    assert draft.chart is None
    assert draft.disclaimer == DISCLAIMER


async def test_missing_confidence_is_reasked() -> None:
    router = ScriptedRouter([_reply(_report(confidence=None)), _reply(_report())])
    update = await _run(router)
    assert update["draft"].confidence is not None
    assert "confidence" in (router.calls[1][1].messages[-1].content or "")


async def test_unparseable_replies_leave_no_draft() -> None:
    router = ScriptedRouter([_reply("nope"), _reply("still nope")])
    assert await _run(router) == {"draft": None}


async def test_no_reask_without_calls_left() -> None:
    router = ScriptedRouter([_reply("nope")])
    ctx = make_ctx(
        make_deps(router), "writer", budget=ModeBudget.deep(), usage=Usage(model_calls=19)
    )
    assert await writer_node(_state(), ctx) == {"draft": None}
    assert len(router.calls) == 1


async def test_nothing_found_is_no_evidence_without_a_call() -> None:
    router = ScriptedRouter([])
    draft = (await _run(router, findings=[], evidence=[], language="en"))["draft"]
    assert draft.status == "no_evidence"
    assert draft.language == "en"
    assert draft.tables == []
    assert router.calls == []


async def test_verified_calculations_reach_the_writer_and_the_check() -> None:
    calc = Calculation(
        cite_id="k:1",
        name="debt_growth",
        description="Net borç değişimi",
        code="result = {}",
        output=Decimal("0.0701298701"),
        verified=True,
    )
    unverified = calc.model_copy(update={"cite_id": "k:2", "verified": False})
    report = _report(points=[{"text": "Net borç %7,0 arttı", "cite_ids": ["k:1"]}])
    router = ScriptedRouter([_reply(report)])

    update = await _run(router, calculations=[calc, unverified])

    assert update["draft"].status == "completed"
    user = router.calls[0][1].messages[1].content or ""
    assert '"k:1"' in user
    assert '"k:2"' not in user
    assert verified_outputs([calc, unverified]) == {"k:1": Decimal("0.0701298701")}


def test_display_numbers_follow_the_report_language() -> None:
    assert display_number(Decimal("0.3508326245251898"), "tr") == "0,35"
    assert display_number(Decimal("1234567.891"), "tr") == "1.234.567,89"
    assert display_number(Decimal("-34.04782032389833"), "en") == "-34.05"


def test_calculation_material_gives_display_values_the_validator_accepts() -> None:
    ratio = Calculation(
        cite_id="k:1", name="growth", code="x", output=Decimal("0.3508326245251898"), verified=True
    )
    change = Calculation(
        cite_id="k:2", name="change", code="x", output=Decimal("-34.0478"), verified=True
    )
    tr = calculation_material(ratio, "tr")
    assert (tr["display"], tr["as_percent"]) == ("0,35", "%35,08")
    assert "as_percent" not in calculation_material(change, "en")

    outputs = verified_outputs([ratio, change])
    report = Report.model_validate(
        {
            "language": "tr",
            "status": "completed",
            "summary": "Özet.",
            "points": [
                {"text": f"Büyüme {tr['as_percent']} oldu", "cite_ids": ["k:1"]},
                {"text": "Marj 34,05 puan düştü", "cite_ids": ["k:2"]},
            ],
            "tables": [
                {
                    "title": "Büyüme",
                    "columns": ["Banka", "Oran"],
                    "rows": [[{"text": "Akbank"}, {"text": tr["display"], "cite_id": "k:1"}]],
                }
            ],
            "citations": [],
            "disclaimer": "x",
        }
    )
    assert validate_report(report, {}, outputs, "tr", derived_ok=False).ok
