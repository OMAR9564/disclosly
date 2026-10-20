"""Metrics, matching and reports of the retrieval eval (contracts/golden-set.md)."""

import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals import retrieval as ev
from ingestion.models import DocType
from retrieval.types import SearchHit, SourceRef


def hit(text: str, ticker: str = "THYAO", period: str = "2025-Q2") -> SearchHit:
    return SearchHit(
        chunk_id="c",
        text=text,
        kind="text",
        source=SourceRef(
            document_id=1,
            ticker=ticker,
            doc_type=DocType.KAP_FINANCIAL_REPORT,
            period=period,
            section="body",
            page=1,
            source_url="https://example.test",
        ),
        score=1.0,
        vector_rank=1,
        fulltext_rank=None,
    )


def case(case_id: str = "tr-1", language: str = "tr", quote: str = "Net borç") -> ev.GoldenCase:
    return ev.GoldenCase.model_validate(
        {
            "id": case_id,
            "language": language,
            "question": "q",
            "filters": {"tickers": ["THYAO"]},
            "expected": [
                {"ticker": "THYAO", "doc_type": "kap_financial_report", "period": "2025-Q2",
                 "page": 14, "quote": quote}
            ],
        }
    )  # fmt: skip


def test_normalise_is_case_space_and_accent_insensitive() -> None:
    assert ev.normalise("  FAVÖK\n marjı ") == "favok marji"
    assert ev.normalise("İŞLETME Iğdır") == ev.normalise("isletme igdir")


def test_hit_needs_same_document_and_quote() -> None:
    expected = case(quote="net  BORÇ").expected[0]
    assert ev.is_hit(hit("Şirketin net borç tutarı"), expected)
    assert not ev.is_hit(hit("Şirketin net borç tutarı", ticker="PGSUS"), expected)
    assert not ev.is_hit(hit("Şirketin net borç tutarı", period="2025-Q3"), expected)
    assert not ev.is_hit(hit("brüt borç"), expected)


def test_first_hit_rank() -> None:
    hits = [hit("x"), hit("y"), hit("net borç azaldı")]
    assert ev.first_hit_rank(hits, case()) == 3
    assert ev.first_hit_rank(hits[:2], case()) is None


def test_score() -> None:
    s = ev.score([1, 2, 6, None])
    assert (s.cases, s.recall_at_5, s.recall_at_10) == (4, 0.5, 0.75)
    assert s.mrr == pytest.approx((1 + 1 / 2 + 1 / 6) / 4)
    assert ev.score([]) == ev.Scores(0, 0.0, 0.0, 0.0)


def test_summarise_by_language() -> None:
    cases = [case("tr-1"), case("tr-2"), case("en-1", "en")]
    result = ev.summarise("v1-600-64", False, cases, [1, None, 2])
    assert result.overall.recall_at_10 == pytest.approx(2 / 3)
    assert result.by_language["tr"].recall_at_10 == 0.5
    assert result.by_language["en"].mrr == 0.5
    data = result.as_dict()
    assert data["k"] == 10 and data["cases"] == 3
    assert set(data["by_language"]) == {"en", "tr"}


def test_report_files(tmp_path: Path) -> None:
    result = ev.summarise("v1-600-64", True, [case()], [1])
    report = ev.build_report([result], "cassette", date(2026, 10, 20))
    path = ev.write_report(report, tmp_path)
    assert path.name == "retrieval-2026-10-20.json"
    data = json.loads(path.read_text())
    assert data["corpus"] == "cassette" and data["configs"][0]["recall_at_10"] == 1.0
    markdown = (tmp_path / "retrieval-2026-10-20.md").read_text()
    assert "| v1-600-64 | on | all | 1 | 1.000 | 1.000 | 1.000 |" in markdown
    assert "| v1-600-64 | on | tr |" in markdown


def _report(recall: float) -> dict[str, object]:
    return {"configs": [{"chunker_version": "v1-600-64", "rerank": False, "recall_at_10": recall}]}


def test_baseline_gate() -> None:
    baseline = {"chunker_version": "v1-600-64", "rerank": False, "recall_at_10": 0.8}
    assert ev.check_baseline(_report(0.8), baseline) is None
    assert ev.check_baseline(_report(0.79), baseline) is None  # inside the 0.02 tolerance
    assert "below baseline" in str(ev.check_baseline(_report(0.7), baseline))
    other = {**baseline, "rerank": True}
    assert "not run" in str(ev.check_baseline(_report(0.9), other))


def test_load_golden(tmp_path: Path) -> None:
    path = tmp_path / "g.jsonl"
    path.write_text(case().model_dump_json() + "\n\n" + case("en-1", "en").model_dump_json())
    assert [c.id for c in ev.load_golden(path)] == ["tr-1", "en-1"]
    path.write_text(case().model_dump_json() + "\n" + case().model_dump_json())
    with pytest.raises(ValueError, match="unique"):
        ev.load_golden(path)
    path.write_text(json.dumps({"id": "x", "language": "de", "question": "q", "expected": []}))
    with pytest.raises(ValidationError):
        ev.load_golden(path)


def test_rerank_option_parsing() -> None:
    assert ev._parse_rerank("on,off") == [True, False]
    with pytest.raises(Exception, match="on, off"):
        ev._parse_rerank("maybe")
