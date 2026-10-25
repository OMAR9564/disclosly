"""verifier_seeded dataset and the graph harness's new metrics (T037)."""

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from agent.graph.state import Calculation
from agent.report import Report
from evals import graph
from evals.graph_cases import (
    GRAPH_DATASETS,
    SEEDED,
    DeepCase,
    GraphCassette,
    GraphOutcome,
    RecordingSandbox,
    ReplaySandbox,
    SandboxCassetteMiss,
    SeededCase,
    load_graph_dataset,
    run_seeded_case,
    sandbox_key,
    write_cassette,
)
from evals.record_agent import RecordingRouter
from llm.types import ChatRequest, ChatResponse, TaskClass
from tests.unit.agent.graph.test_analyst_calcs import LocalSandbox

CASES: list[SeededCase] = load_graph_dataset(SEEDED)


class Judge:
    """Claim support: supports every finding except those whose text gives a cause or rank."""

    async def chat(self, request: ChatRequest, *, task_class: TaskClass) -> ChatResponse:
        data = json.loads((request.messages[1].content or "").split("\n\n")[1])
        claims = [
            {
                "finding_id": f["id"],
                "supported": not any(
                    w in f["text"] for w in ("because", "kaynaklandı", "highest", "en yüksek")
                ),
                "reason": "",
            }
            for f in data["findings"]
        ]
        return ChatResponse(content=json.dumps({"claims": claims}), provider="g", model="m")


def test_dataset_size_languages_and_defects() -> None:
    defective = [c for c in CASES if c.expected_rejected]
    assert len(defective) >= 20
    assert {c.language for c in CASES} == {"tr", "en"}
    kinds = Counter(c.defect for c in defective)
    assert set(kinds) == {
        "wrong_output",
        "wrong_fact_input",
        "chunk_number",
        "uncited_number",
        "unsupported_claim",
    }
    assert all(kinds[k] >= 4 for k in kinds)
    assert all(c.expected_failure is None for c in CASES if not c.expected_rejected)
    assert all(c.defect is None for c in CASES if not c.expected_rejected)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_case_ids_and_citations(case: SeededCase) -> None:
    assert case.id.startswith(f"{case.language}-")
    known = {item.cite_id for item in case.evidence}
    assert all(c in known for f in case.findings for c in f.cite_ids)
    for calc in case.calculations:
        assert all(i.cite_id in known for i in calc.inputs)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
async def test_verifier_meets_each_expectation(case: SeededCase) -> None:
    verdict = await run_seeded_case(case, Judge(), LocalSandbox())
    result = graph.score_seeded(case, verdict, 1)
    assert result.passed, result.notes


async def _record(case: SeededCase, directory: Path) -> None:
    router = RecordingRouter(Judge())
    sandbox = RecordingSandbox(LocalSandbox())
    await run_seeded_case(case, router, sandbox)
    write_cassette(
        GraphCassette(
            case_id=case.id,
            dataset=SEEDED,
            interactions=router.interactions,
            tools=[],
            sandbox=sandbox.records,
        ),
        directory,
    )


async def test_suite_scores_seeded_drafts_from_cassettes(tmp_path: Path) -> None:
    datasets, cassettes = tmp_path / "datasets", tmp_path / "cassettes"
    datasets.mkdir()
    for name in GRAPH_DATASETS:
        (datasets / f"{name}.jsonl").write_text("", encoding="utf-8")
    picked = [c for c in CASES if c.defect in (None, "wrong_output", "unsupported_claim")][:6]
    (datasets / f"{SEEDED}.jsonl").write_text(
        "".join(c.model_dump_json() + "\n" for c in picked), encoding="utf-8"
    )
    for case in picked:
        await _record(case, cassettes)

    suite = await graph.run_suite(datasets, cassettes)
    assert suite.failures == []
    scores = graph.metrics(suite.results)
    assert scores["seeded_rejection_rate"] == 1.0
    assert scores["seeded_false_rejection_rate"] == 0.0

    # A stale sandbox record or a missing cassette fails the suite.
    wrong = next(c for c in picked if c.defect == "wrong_output")
    path = cassettes / f"{wrong.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["sandbox"] = []
    path.write_text(json.dumps(data), encoding="utf-8")
    (cassettes / f"{picked[0].id}.json").unlink()
    suite = await graph.run_suite(datasets, cassettes)
    assert any("stale cassette" in f for f in suite.failures)
    assert any("no recorded cassette" in f for f in suite.failures)


async def test_replay_sandbox_matches_by_batch() -> None:
    inner = LocalSandbox()
    recording = RecordingSandbox(inner)
    run = await recording.run("result = {'x': inputs['a'] * 2}", {"a": Decimal(2)}, timeout_s=5)
    assert run.outputs == {"x": Decimal("4.0")}
    replayed = ReplaySandbox(recording.records)
    again = await replayed.run("result = {'x': inputs['a'] * 2}", {"a": Decimal(2)}, timeout_s=5)
    assert again.outputs == run.outputs
    with pytest.raises(SandboxCassetteMiss):
        await replayed.run("result = {'x': inputs['a'] * 2}", {"a": Decimal(2)}, timeout_s=5)
    assert sandbox_key("x", {"a": Decimal(1)}) != sandbox_key("x", {"a": Decimal(2)})


def _report(text: str, cite: str) -> Report:
    return Report.model_validate(
        {
            "language": "tr",
            "status": "completed",
            "summary": f"{text} [1].",
            "citations": [{"marker": 1, "cite_id": cite}],
        }
    )


def test_derived_numbers_must_come_from_cited_calculations() -> None:
    case = DeepCase.model_validate(
        {
            "id": "tr-deep-x",
            "question": "THY net borç / FAVÖK?",
            "language": "tr",
            "expected_numbers": [
                {"value": 2.58, "tolerance": 0.01, "derived": True},
                {"value": 25.47, "tolerance": 0.1, "derived": True},
                {"value": 489.94, "tolerance": 0.01},
            ],
        }
    )
    calc = Calculation(
        cite_id="k:1", name="r", code="x", output=Decimal("2.5799"), verified=True, checked=True
    )
    values: dict[str, Any] = {"calculations": [calc]}
    assert graph.derived_numbers(_report("Oran 2,58 oldu", "k:1"), case, values) == (1, 1)
    # A negative output stated as a magnitude ("fell by 2,58") still comes from k:1.
    falling = {"calculations": [calc.model_copy(update={"output": Decimal("-2.5799")})]}
    assert graph.derived_numbers(_report("Oran 2,58 düştü", "k:1"), case, falling) == (1, 1)
    # Stated but not from a cited verified calculation.
    assert graph.derived_numbers(_report("Oran 2,58 oldu", "f:1"), case, values) == (1, 0)
    unverified = {"calculations": [calc.model_copy(update={"verified": False})]}
    assert graph.derived_numbers(_report("Oran 2,58 oldu", "k:1"), case, unverified) == (1, 0)
    # A ratio output shown as a percentage still counts.
    margin = calc.model_copy(update={"output": Decimal("0.2547")})
    report = _report("Marj %25,47 oldu", "k:1")
    assert graph.derived_numbers(report, case, {"calculations": [margin]}) == (1, 1)
    # Not stated at all: nothing to check.
    assert graph.derived_numbers(_report("Borç arttı", "k:1"), case, values) == (0, 0)

    outcome = GraphOutcome({"draft": _report("Oran 2,58 oldu", "f:1"), "calculations": [calc]})
    result = graph.score_case(case, "deep_golden", outcome)
    assert (result.derived_stated, result.derived_from_calculations) == (1, 0)
    assert graph.metrics([result])["derived_from_calculations"] == 0.0


def test_gate_on_derived_and_seeded_rates() -> None:
    perfect = {
        "citation_coverage": 1.0,
        "refusal_rate": 1.0,
        "injection_pass_rate": 1.0,
        "numeric_match_rate": 1.0,
        "derived_from_calculations": 1.0,
        "seeded_rejection_rate": 0.95,
    }
    baseline = {"numeric_match_rate": 1.0}
    assert graph.check_gate(perfect, baseline) == []
    errors = graph.check_gate({**perfect, "derived_from_calculations": 0.5}, baseline)
    assert errors == ["derived_from_calculations 0.5000 is below 1.0"]
    errors = graph.check_gate({**perfect, "seeded_rejection_rate": 0.85}, baseline)
    assert errors == ["seeded_rejection_rate 0.8500 is below 0.9"]
