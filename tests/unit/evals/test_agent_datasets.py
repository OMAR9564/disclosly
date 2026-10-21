"""Schema checks for the agent eval datasets (T020)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.agent_cases import (
    DATASETS,
    AdviceCase,
    Cassette,
    GoldenCase,
    InjectionCase,
    RecordedTool,
    ReplayTools,
    ToolCassetteMiss,
    load_dataset,
    poisoned_item,
    read_cassette,
    tool_key,
    write_cassette,
)
from llm.types import ToolCall


def test_golden_set_shape() -> None:
    cases: list[GoldenCase] = load_dataset("answers_golden")
    answered = [c for c in cases if c.expected_status == "completed"]
    assert sum(c.language == "tr" for c in answered) == 10
    assert sum(c.language == "en" for c in answered) == 10
    assert all(c.expected_numbers for c in answered)
    not_covered = [c for c in cases if c.expected_status == "no_evidence"]
    assert len(not_covered) == 3
    assert all(not c.expected_numbers for c in not_covered)


def test_advice_set_shape() -> None:
    cases: list[AdviceCase] = load_dataset("advice_refusal")
    assert len(cases) == 20
    assert {c.language for c in cases} == {"tr", "en"}


def test_injection_set_shape() -> None:
    cases: list[InjectionCase] = load_dataset("injection")
    assert len(cases) == 15
    assert all(c.forbidden_strings for c in cases)
    item = poisoned_item(cases[0])
    assert item.cite_id.startswith("c:poisoned-")


@pytest.mark.parametrize("name", DATASETS)
def test_ids_are_unique_and_prefixed(name: str) -> None:
    cases = load_dataset(name)  # type: ignore[arg-type]
    for case in cases:
        assert case.id.startswith(f"{case.language}-")


def test_rejects_bad_rows(tmp_path: Path) -> None:
    (tmp_path / "advice_refusal.jsonl").write_text(
        '{"id": "x", "question": "q", "language": "de"}\n'
    )
    with pytest.raises(ValidationError):
        load_dataset("advice_refusal", tmp_path)
    row = '{"id": "x", "question": "abc", "language": "tr"}\n'
    (tmp_path / "advice_refusal.jsonl").write_text(row * 2)
    with pytest.raises(ValueError, match="duplicate"):
        load_dataset("advice_refusal", tmp_path)


async def test_cassette_round_trip_and_tool_replay(tmp_path: Path) -> None:
    entry = RecordedTool(
        key=tool_key("search_documents", {"query": "x"}),
        tool="search_documents",
        content="No results.",
        summary="x",
        count=0,
        items=[],
    )
    cassette = Cassette(case_id="tr-a", dataset="advice_refusal", interactions=[], tools=[entry])
    assert read_cassette(write_cassette(cassette, tmp_path)) == cassette
    tools = ReplayTools([entry])
    result = await tools.dispatch(
        ToolCall(id="1", name="search_documents", arguments={"query": "x"})
    )
    assert result.content == "No results."
    with pytest.raises(ToolCassetteMiss):
        await tools.dispatch(ToolCall(id="2", name="search_documents", arguments={"query": "x"}))
