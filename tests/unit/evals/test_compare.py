"""Single agent vs multi-agent comparison (T051, SC-010): pairing, metrics and the report."""

from pathlib import Path
from typing import Any

import pytest

from evals import compare
from evals.agent_cases import CASSETTES_DIR as SINGLE_DIR
from evals.compare import CaseRow, Comparison, EngineResult
from evals.graph_cases import CASSETTES_DIR as GRAPH_DIR
from llm.testing import CassetteMiss


def _golden(matched: int, *, calls: int = 2, status: str = "completed", **kw: Any) -> EngineResult:
    return EngineResult(status, calls, numbers_expected=2, numbers_matched=matched, **kw)


def test_scores_compare_answers_then_support() -> None:
    assert _golden(2).score() == (1.0, True)
    assert _golden(1, figures_total=3, figures_supported=2).score() == (0.5, False)
    assert EngineResult("refused", 1, passed=True).score() == (1.0, True)
    assert EngineResult("partial", 3).score() == (0.0, True)
    assert EngineResult("completed", 3).score() == (1.0, True)


def test_outcome_is_from_the_graph_side_and_needs_both_engines() -> None:
    assert CaseRow("a", "answers_golden", _golden(1), _golden(2)).outcome() == "win"
    assert CaseRow("a", "answers_golden", _golden(2), _golden(1)).outcome() == "loss"
    # More model calls alone do not make a loss.
    assert CaseRow("a", "answers_golden", _golden(2), _golden(2, calls=8)).outcome() == "tie"
    assert CaseRow("a", "deep_golden", None, _golden(2)).outcome() is None


def test_engine_metrics() -> None:
    metrics = compare.engine_metrics(
        [
            ("answers_golden", _golden(1, elapsed_ms=1000)),
            (
                "deep_golden",
                _golden(
                    2,
                    calls=6,
                    status="partial",
                    derived_stated=2,
                    derived_verified=1,
                    elapsed_ms=3000,
                ),
            ),
            ("advice_refusal", EngineResult("refused", 1, passed=True)),
            (
                "injection",
                EngineResult("completed", 2, passed=False, figures_total=2, figures_supported=1),
            ),
        ]
    )
    assert metrics["cases"] == 4
    assert metrics["numeric_match_rate"] == 0.5
    assert metrics["deep_numeric_match_rate"] == 1.0
    assert metrics["citation_coverage"] == 0.5
    assert metrics["verified_number_rate"] == 0.5
    assert metrics["refusal_rate"] == 1.0
    assert metrics["injection_pass_rate"] == 0.0
    assert metrics["partial_rate"] == 0.5
    assert metrics["model_calls_per_run"] == 2.75
    assert metrics["max_model_calls"] == 6
    assert metrics["latency_recorded"] == "2/4"
    assert (metrics["latency_p50_ms"], metrics["latency_p95_ms"]) == (1000, 3000)


def test_nothing_measured_is_not_a_perfect_score() -> None:
    metrics = compare.engine_metrics([("answers_golden", _golden(2))])
    assert metrics["verified_number_rate"] is None
    assert metrics["refusal_rate"] is None
    assert metrics["latency_p50_ms"] is None


async def test_missing_and_stale_cassettes_are_listed_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = [
        ("answers_golden", _Case("both")),
        ("deep_golden", _Case("graph-only-cassette")),
        ("injection", _Case("stale")),
    ]
    monkeypatch.setattr(compare, "shared_cases", lambda datasets_dir: cases)

    async def single(case: Any, dataset: str, cassettes: Path) -> EngineResult | None:
        if case.id == "stale":
            raise CassetteMiss("prompt changed")
        return _golden(1) if case.id == "both" else None

    async def graph(case: Any, dataset: str, cassettes: Path) -> EngineResult | None:
        return _golden(2)

    monkeypatch.setattr(compare, "run_single", single)
    monkeypatch.setattr(compare, "run_graph", graph)
    comparison = await compare.run_comparison(Path("d"), Path("s"), Path("g"))
    assert [r.case_id for r in comparison.rows] == ["both", "graph-only-cassette", "stale"]
    assert comparison.rows[1].notes == ["single: no cassette"]
    assert comparison.rows[2].notes == ["single: stale cassette"]
    assert len(comparison.stale) == 1

    report = compare.build_report(comparison, "2026-10-26")
    assert report["summary"]["paired_cases"] == 1
    assert report["summary"]["graph_vs_single"]["win"] == 1
    text = compare.report_markdown(report)
    assert "# Single agent vs multi-agent graph, 2026-10-26" in text
    assert "## Not compared" in text
    assert "- deep_golden/graph-only-cassette: single: no cassette" in text
    assert "## Stale cassettes" in text
    assert "| verified_number_rate | n/a | n/a |" in text


def test_shared_cases_need_both_engines() -> None:
    cases = compare.shared_cases()
    assert {d for d, _ in cases} == set(compare.SHARED_DATASETS)
    assert all({"single", "graph"} <= set(c.engines) for _, c in cases)


def test_main_writes_the_dated_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(*args: Any) -> Comparison:
        return Comparison([CaseRow("a", "answers_golden", _golden(1), _golden(2))])

    monkeypatch.setattr(compare, "run_comparison", fake)
    assert compare.main(["--replay", "--reports", str(tmp_path)]) == 0
    written = sorted(p.name for p in tmp_path.iterdir())
    assert len(written) == 2 and written[1].endswith("-single-vs-multi.md")


class _Case:
    def __init__(self, case_id: str) -> None:
        self.id = case_id
        self.engines = ["single", "graph"]


async def test_replays_a_recorded_case_on_both_engines() -> None:
    dataset, case = next((d, c) for d, c in compare.shared_cases() if d == "answers_golden")
    single = await compare.run_single(case, dataset, SINGLE_DIR)
    graph = await compare.run_graph(case, dataset, GRAPH_DIR)
    assert single is not None and graph is not None
    assert single.numbers_expected == graph.numbers_expected == len(case.expected_numbers)
    assert await compare.run_single(case, dataset, Path("/nonexistent")) is None
    assert await compare.run_graph(case, dataset, Path("/nonexistent")) is None
