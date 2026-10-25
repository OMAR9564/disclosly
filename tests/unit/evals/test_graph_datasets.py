"""Graph eval datasets (T024): schemas, counts, languages and the cases' own checks."""

import pytest

from agent.advice import detect_language, is_advice_request
from evals.agent_cases import AdviceCase, InjectionCase, load_dataset
from evals.graph_cases import DeepCase, GraphCassette, load_graph_dataset


def test_deep_golden_has_eight_cases_per_language() -> None:
    cases = load_graph_dataset("deep_golden")
    assert all(isinstance(c, DeepCase) for c in cases)
    assert sum(c.language == "tr" for c in cases) >= 8
    assert sum(c.language == "en" for c in cases) >= 8


@pytest.mark.parametrize("case", load_graph_dataset("deep_golden"), ids=lambda c: c.id)
def test_deep_golden_case(case: DeepCase) -> None:
    assert case.id.startswith(f"{case.language}-deep-")
    assert detect_language(case.question) == case.language
    assert not is_advice_request(case.question)
    assert case.expected_mode == "deep"
    if case.expected_status == "no_evidence":
        # The empty-watchlist case: nothing to show, nothing guessed.
        assert case.memory is not None and not case.memory.watchlist
        assert case.expected_tickers == []
        return
    assert case.expected_tables >= 1
    assert case.expected_numbers
    assert all(n.tolerance > 0 for n in case.expected_numbers)


def test_deep_golden_memory_cases() -> None:
    """T048: at least four watchlist or period-less cases, each with its memory fixture."""
    cases = [c for c in load_graph_dataset("deep_golden") if c.memory is not None]
    assert len(cases) >= 4
    watchlist = [c for c in cases if c.expected_tickers is not None and c.memory.watchlist]
    period_less = [c for c in cases if c.memory.default_period_range is not None]
    assert len(watchlist) >= 2 and len(period_less) >= 2
    assert any(c.expected_tickers == [] for c in cases)  # empty watchlist
    for case in watchlist:
        assert sorted(case.expected_tickers or []) == sorted(case.memory.watchlist)
        assert any(w in case.question.casefold() for w in ("takip listem", "my watchlist"))
    for case in period_less:
        assert case.memory.latest_period is not None
        assert not any(ch.isdigit() for ch in case.question)


def test_deep_golden_covers_comparisons_trends_and_ratios() -> None:
    cases = load_graph_dataset("deep_golden")
    assert sum(c.expected_chart for c in cases) >= 6
    words = ("karşılaştır", "compare")
    assert sum(any(w in c.question.casefold() for w in words) for c in cases) >= 6
    ratios = ("marj", "margin", "yüzde", "percent", "net debt")
    assert sum(any(w in c.question.casefold() for w in ratios) for c in cases) >= 4


def test_graph_advice_cases() -> None:
    graph_only = [c for c in load_dataset("advice_refusal") if c.engines == ["graph"]]
    assert len(graph_only) >= 5
    for case in graph_only:
        assert isinstance(case, AdviceCase)
        assert detect_language(case.question) == case.language
        if case.plan_edit_goal is not None:
            # The question is fine; the advice hides in the edited step.
            assert not is_advice_request(case.question)
            assert is_advice_request(case.plan_edit_goal)
        else:
            assert is_advice_request(case.question)
    assert any(c.plan_edit_goal for c in graph_only)


def test_graph_injection_cases() -> None:
    graph_only = [c for c in load_dataset("injection") if c.engines == ["graph"]]
    assert len(graph_only) >= 5
    for case in graph_only:
        assert isinstance(case, InjectionCase)
        assert detect_language(case.question) == case.language
        assert all(s and s not in case.question for s in case.forbidden_strings)


def test_memory_injection_cases() -> None:
    """T048: at least three injections that try to change memory from retrieved text."""
    cases = [c for c in load_dataset("injection") if c.memory is not None]
    assert len(cases) >= 3
    for case in cases:
        assert case.engines == ["graph"]
        assert case.memory is not None and case.memory.watchlist
        text = case.poisoned_chunk.casefold()
        assert any(w in text for w in ("takip listesi", "watchlist"))


def test_single_engine_cases_unchanged() -> None:
    # Cases from Phase 3 still run on both engines.
    golden = load_dataset("answers_golden")
    assert all(c.engines == ["single", "graph"] for c in golden)


def test_graph_loader_keeps_graph_cases_only() -> None:
    assert all("graph" in c.engines for c in load_graph_dataset("advice_refusal"))
    assert len(load_graph_dataset("injection")) == len(load_dataset("injection"))


def test_cassette_schema() -> None:
    cassette = GraphCassette.model_validate(
        {"case_id": "x", "dataset": "deep_golden", "interactions": [], "tools": []}
    )
    assert cassette.sandbox == []
    assert cassette.decision is None
