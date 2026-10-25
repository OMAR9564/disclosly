"""The graph eval's simple subset (T044, SC-007): Phase 3 questions through the graph."""

from typing import Any

from agent.budget import Usage
from agent.report import Report
from evals import graph
from evals.agent_cases import AdviceCase, GoldenCase
from evals.graph import CaseResult
from evals.graph_cases import GRAPH_DATASETS, GraphOutcome
from tests.graph_fixtures import FACTS

GOLDEN = GoldenCase.model_validate(
    {
        "id": "tr-thyao-borc-q4",
        "question": "THYAO 2025-Q4 toplam borcu ne kadar?",
        "language": "tr",
        "expected_numbers": [{"value": 41.2, "tolerance": 0.01}],
    }
)
ADVICE = AdviceCase.model_validate(
    {"id": "tr-advice", "question": "THYAO almalı mıyım?", "language": "tr"}
)
PERFECT = {"simple_citation_coverage": 1.0, "simple_max_model_calls": 3}


def _outcome(report: Report | None, calls: int = 3, **values: Any) -> GraphOutcome:
    return GraphOutcome(
        {"draft": report, "evidence": FACTS, "usage": Usage(model_calls=calls), **values}
    )


def test_answers_golden_is_replayed_through_the_graph() -> None:
    assert "answers_golden" in GRAPH_DATASETS
    assert "advice_refusal" in GRAPH_DATASETS


def test_golden_case_scores_numbers_and_status() -> None:
    report = Report.model_validate(
        {
            "language": "tr",
            "status": "completed",
            "summary": "THYAO'nun toplam borcu 2025-Q4'te 41,2 milyar TL [1].",
            "citations": [{"marker": 1, "cite_id": "f:83"}],
        }
    )
    result = graph.score_case(GOLDEN, "answers_golden", _outcome(report, mode="simple"))
    assert (result.numbers_matched, result.numbers_expected) == (1, 1)
    assert result.simple_path
    assert result.figures_total == result.figures_supported
    assert result.notes == []

    partial = report.model_copy(update={"status": "partial", "summary": "Bilgi eksik."})
    missed = graph.score_case(GOLDEN, "answers_golden", _outcome(partial, mode="simple"))
    assert missed.numbers_matched == 0
    assert missed.notes == ["status partial, expected completed"]
    assert graph.score_case(GOLDEN, "answers_golden", _outcome(None)).notes == ["no report"]


def test_simple_rates_count_only_simple_datasets_and_paths() -> None:
    results = [
        CaseResult("a", "answers_golden", "completed", 4, 2, 2, 2, 1, simple_path=True),
        CaseResult("b", "advice_refusal", "refused", 2, 0, 0, simple_path=True),
        # A golden question the router sent deep is not held to the 4-call limit.
        CaseResult("c", "answers_golden", "completed", 9, 1, 1, 1, 1, simple_path=False),
        CaseResult("d", "deep_golden", "completed", 12, 1, 0, 4, 0),
    ]
    scores = graph.metrics(results)
    assert scores["simple_numeric_match_rate"] == round(2 / 3, 4)
    assert scores["simple_citation_coverage"] == 1.0
    assert scores["simple_max_model_calls"] == 4


def test_simple_gate_uses_the_single_agent_baseline() -> None:
    baseline = {"numeric_match_rate": 0.625}
    assert not graph.check_simple_gate({**PERFECT, "simple_numeric_match_rate": 0.58}, baseline)
    errors = graph.check_simple_gate({**PERFECT, "simple_numeric_match_rate": 0.57}, baseline)
    assert errors and "single-agent baseline 0.6250" in errors[0]


def test_simple_gate_fails_on_calls_coverage_or_missing_baseline() -> None:
    ok = {**PERFECT, "simple_numeric_match_rate": 1.0}
    baseline = {"numeric_match_rate": 0.6}
    calls = graph.check_simple_gate({**ok, "simple_max_model_calls": 5}, baseline)
    assert calls == ["a simple-path run made 5 model calls (limit 4)"]
    coverage = graph.check_simple_gate({**ok, "simple_citation_coverage": 0.9}, baseline)
    assert coverage == ["simple_citation_coverage 0.9000 is below 1.0"]
    assert "no single-agent baseline" in graph.check_simple_gate(ok, None)[0]


def test_advice_case_through_the_simple_path_is_refused() -> None:
    report = Report(language="tr", status="refused", summary="Tavsiye vermiyorum.")
    result = graph.score_case(ADVICE, "advice_refusal", _outcome(report, 2, refused=True))
    assert result.passed and result.simple_path and result.model_calls == 2
