"""Record cassettes from the fixture corpus, replay them on an empty database, gate on baseline."""

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from evals import record_retrieval, retrieval
from evals.retrieval import GoldenCase
from retrieval.embeddings import QUERY_PREFIX
from tests.integration.retrieval.corpus import VERSION, Corpus, basis

pytestmark = pytest.mark.integration

AXES = {"FAVÖK marjı ne oldu?": 1, "Apple net debt": 0, "fleet expansion aircraft": 2}
CASES = [
    {"id": "tr-favok", "language": "tr", "question": "FAVÖK marjı ne oldu?",
     "filters": {"tickers": ["THYAO"]},
     "expected": [{"ticker": "THYAO", "doc_type": "kap_financial_report",
                   "period": "2025-Q3", "quote": "favok marji yuzde 25"}]},
    {"id": "tr-order", "language": "tr", "question": "fleet expansion aircraft",
     "expected": [{"ticker": "THYAO", "doc_type": "kap_material_event",
                   "period": "2024-Q2", "quote": "uçak siparişi"}]},
    {"id": "en-debt", "language": "en", "question": "Apple net debt",
     "filters": {"tickers": ["AAPL"], "language": "en"},
     "expected": [{"ticker": "AAPL", "doc_type": "10-K",
                   "period": "2024-FY", "quote": "Net debt decreased"}]},
    {"id": "en-missing", "language": "en", "question": "not in fixture",
     "expected": [{"ticker": "MSFT", "doc_type": "10-K", "period": "2024-FY", "quote": "x"}]},
]  # fmt: skip


class AxisEmbedder:
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [basis(AXES[t.removeprefix(QUERY_PREFIX)]) for t in texts]


class LengthReranker:
    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        return [float(len(t)) for t in texts]


def _empty(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE companies, documents, sections, chunks CASCADE"))


def test_record_replay_and_gate(corpus: Corpus, tmp_path: Path) -> None:
    golden = tmp_path / "golden.jsonl"
    golden.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in CASES) + "\n")
    out, reports, baseline = tmp_path / "tape", tmp_path / "reports", tmp_path / "baseline.json"

    recorded = record_retrieval.record(
        corpus.engine,
        retrieval.load_golden(golden),
        out,
        embedder=AxisEmbedder(),
        reranker=LengthReranker(),
        embedding_model="fake-e5",
        rerank_model="fake-ce",
        chunker_version=VERSION,
    )
    assert recorded == ["tr-favok", "tr-order", "en-debt"]  # MSFT is not in the corpus
    lines = [json.loads(line) for line in (out / "corpus.jsonl").read_text().splitlines()]
    assert {r["row"]["id"] for r in lines if r["table"] == "documents"} == {10, 11, 21}
    chunks = [r["row"] for r in lines if r["table"] == "chunks"]
    assert all(c["chunker_version"] == VERSION and "tsv" not in c for c in chunks)
    query = json.loads((out / "queries" / "tr-favok.json").read_text())
    assert query["model"] == "fake-e5" and len(query["vector"]) == 384
    rerank = json.loads((out / "rerank" / "en-debt.json").read_text())
    assert rerank["scores"] and all(isinstance(v, float) for v in rerank["scores"].values())

    args = ["--cassettes-only", "--golden", str(golden), "--cassettes", str(out)]
    args += ["--baseline", str(baseline), "--reports", str(reports), "--rerank", "on,off"]

    _empty(corpus.engine)
    assert retrieval.main([*args, "--write-baseline"]) == 0
    stored = json.loads(baseline.read_text())
    assert (stored["chunker_version"], stored["rerank"], stored["recall_at_10"]) == (
        VERSION,
        False,
        1.0,
    )
    (report_file,) = reports.glob("retrieval-*.json")
    report = json.loads(report_file.read_text())
    assert report["corpus"] == "cassette"
    assert [(c["rerank"], c["cases"]) for c in report["configs"]] == [(True, 3), (False, 3)]
    assert set(report["configs"][1]["by_language"]) == {"en", "tr"}

    _empty(corpus.engine)
    assert retrieval.main(args) == 0  # unchanged: passes

    # Injected regression: the chunk with the Turkish order quote lost its text (as a broken
    # chunker would do), so that case is no longer found.
    tape = (out / "corpus.jsonl").read_text(encoding="utf-8")
    assert "uçak siparişi" in tape
    (out / "corpus.jsonl").write_text(tape.replace("uçak siparişi", "sipariş"), encoding="utf-8")
    _empty(corpus.engine)
    assert retrieval.main(args) == 1

    # The loader never writes into a database that already has chunks.
    with pytest.raises(RuntimeError, match="empty database"):
        retrieval.main(args)


def test_missing_baseline_fails(corpus: Corpus, tmp_path: Path) -> None:
    golden = tmp_path / "golden.jsonl"
    golden.write_text(json.dumps(CASES[0], ensure_ascii=False) + "\n")
    out = tmp_path / "tape"
    record_retrieval.record(
        corpus.engine,
        [GoldenCase.model_validate(CASES[0])],
        out,
        embedder=AxisEmbedder(),
        reranker=LengthReranker(),
        embedding_model="m",
        rerank_model="r",
        chunker_version=VERSION,
    )
    _empty(corpus.engine)
    args = ["--cassettes-only", "--golden", str(golden), "--cassettes", str(out)]
    args += ["--baseline", str(tmp_path / "none.json"), "--reports", str(tmp_path / "r")]
    assert retrieval.main([*args, "--rerank", "off"]) == 1
