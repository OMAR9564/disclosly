"""Retrieval eval (contracts/golden-set.md): recall@5, recall@10 and MRR per configuration.

Full corpus (laptop or VPS, real models, `DATABASE_URL`):

    python -m evals.retrieval --configs v1-400-64,v1-600-64,v1-800-64 --rerank on,off

CI subset (no model, no network): loads the cassette corpus into an empty, migrated database,
replays recorded query vectors and rerank scores, and checks recall@10 against the baseline:

    python -m evals.retrieval --cassettes-only
"""

import argparse
import asyncio
import json
import sys
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, func, insert, select

from ingestion import schema
from ingestion.models import DocType
from retrieval.types import SearchFilters, SearchHit

EVALS_DIR = Path(__file__).parent
GOLDEN_PATH = EVALS_DIR / "datasets" / "retrieval_golden.jsonl"
CASSETTES_DIR = EVALS_DIR / "cassettes" / "retrieval"
BASELINE_PATH = EVALS_DIR / "baselines" / "retrieval.json"
REPORTS_DIR = EVALS_DIR / "reports"
SEARCH_DEPTH = 10
BASELINE_TOLERANCE = 0.02

Language = Literal["tr", "en"]


class Expected(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    doc_type: DocType
    period: str
    page: int | None = None
    quote: str = Field(min_length=1)


class GoldenCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    language: Language
    question: str = Field(min_length=1)
    filters: SearchFilters = SearchFilters()
    expected: list[Expected] = Field(min_length=1)


def load_golden(path: Path = GOLDEN_PATH) -> list[GoldenCase]:
    cases = [
        GoldenCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("golden case IDs must be unique")
    return cases


# ---------------------------------------------------------------------------------- metrics


def normalise(text: str) -> str:
    """Case-folded, whitespace-collapsed and accent-insensitive ("FAVÖK" == "favok")."""
    decomposed = unicodedata.normalize("NFKD", text.replace("ı", "i").replace("İ", "I"))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().split())


def is_hit(hit: SearchHit, expected: Expected) -> bool:
    """Same document (ticker, type, period) and the chunk contains the quote."""
    source = hit.source
    return (
        source.ticker == expected.ticker
        and source.doc_type == expected.doc_type
        and source.period == expected.period
        and normalise(expected.quote) in normalise(hit.text)
    )


def first_hit_rank(hits: Sequence[SearchHit], case: GoldenCase) -> int | None:
    for rank, hit in enumerate(hits, start=1):
        if any(is_hit(hit, e) for e in case.expected):
            return rank
    return None


@dataclass(frozen=True)
class Scores:
    cases: int
    recall_at_5: float
    recall_at_10: float
    mrr: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "cases": self.cases,
            "recall_at_5": round(self.recall_at_5, 4),
            "recall_at_10": round(self.recall_at_10, 4),
            "mrr": round(self.mrr, 4),
        }


def score(ranks: Sequence[int | None]) -> Scores:
    n = len(ranks)
    if n == 0:
        return Scores(0, 0.0, 0.0, 0.0)
    found = [r for r in ranks if r is not None]
    return Scores(
        cases=n,
        recall_at_5=sum(r <= 5 for r in found) / n,
        recall_at_10=sum(r <= 10 for r in found) / n,
        mrr=sum(1 / r for r in found if r <= SEARCH_DEPTH) / n,
    )


@dataclass(frozen=True)
class ConfigResult:
    chunker_version: str
    rerank: bool
    k: int
    overall: Scores
    by_language: dict[str, Scores]

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunker_version": self.chunker_version,
            "rerank": self.rerank,
            "k": self.k,
            **{k: v for k, v in self.overall.as_dict().items() if k != "cases"},
            "cases": self.overall.cases,
            "by_language": {lang: s.as_dict() for lang, s in sorted(self.by_language.items())},
        }


def summarise(
    chunker_version: str, rerank: bool, cases: Sequence[GoldenCase], ranks: Sequence[int | None]
) -> ConfigResult:
    by_language: dict[str, list[int | None]] = {}
    for case, rank in zip(cases, ranks, strict=True):
        by_language.setdefault(case.language, []).append(rank)
    return ConfigResult(
        chunker_version=chunker_version,
        rerank=rerank,
        k=SEARCH_DEPTH,
        overall=score(ranks),
        by_language={lang: score(r) for lang, r in by_language.items()},
    )


# ----------------------------------------------------------------------------------- report


def build_report(results: Iterable[ConfigResult], corpus: str, day: date) -> dict[str, Any]:
    return {"date": day.isoformat(), "corpus": corpus, "configs": [r.as_dict() for r in results]}


def report_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        f"# Retrieval eval {report['date']} ({report['corpus']} corpus)",
        "",
        "| chunker | rerank | lang | cases | recall@5 | recall@10 | MRR |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in report["configs"]:
        rows = [("all", c), *sorted(c["by_language"].items())]
        for lang, s in rows:
            lines.append(
                f"| {c['chunker_version']} | {'on' if c['rerank'] else 'off'} | {lang} "
                f"| {s['cases']} | {s['recall_at_5']:.3f} | {s['recall_at_10']:.3f} "
                f"| {s['mrr']:.3f} |"
            )
    return "\n".join(lines) + "\n"


def write_report(report: Mapping[str, Any], directory: Path = REPORTS_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stem = directory / f"retrieval-{report['date']}"
    stem.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    stem.with_suffix(".md").write_text(report_markdown(report), encoding="utf-8")
    return stem.with_suffix(".json")


def baseline_from(report: Mapping[str, Any], chunker_version: str) -> dict[str, Any]:
    for c in report["configs"]:
        if c["chunker_version"] == chunker_version and not c["rerank"]:
            return {
                "chunker_version": chunker_version,
                "rerank": False,
                "recall_at_10": c["recall_at_10"],
                "date": report["date"],
            }
    raise RuntimeError(f"no {chunker_version} run without rerank to use as the baseline")


def check_baseline(
    report: Mapping[str, Any], baseline: Mapping[str, Any], tolerance: float = BASELINE_TOLERANCE
) -> str | None:
    """Error message when the baseline config's recall@10 dropped by more than `tolerance`."""
    for c in report["configs"]:
        if (c["chunker_version"], c["rerank"]) == (baseline["chunker_version"], baseline["rerank"]):
            floor = float(baseline["recall_at_10"]) - tolerance
            if c["recall_at_10"] < floor:
                return (
                    f"recall@10 {c['recall_at_10']:.3f} is below baseline "
                    f"{baseline['recall_at_10']:.3f} - {tolerance}"
                )
            return None
    return f"baseline config {baseline['chunker_version']} rerank={baseline['rerank']} not run"


# -------------------------------------------------------------------------------- cassettes

CORPUS_TABLES = ("companies", "documents", "sections", "chunks")


def load_cassette_corpus(engine: Engine, path: Path) -> list[str]:
    """Insert `corpus.jsonl` rows into an empty corpus; returns the chunker versions found.

    Lines look like `{"table": "chunks", "row": {...}}`. Refuses a database that already has
    chunks, so it never mixes the fixture corpus into a real one.
    """
    with engine.connect() as conn:
        if conn.execute(select(func.count()).select_from(schema.chunks)).scalar_one():
            raise RuntimeError("cassette corpus needs an empty database (chunks is not empty)")
    rows: dict[str, list[dict[str, Any]]] = {t: [] for t in CORPUS_TABLES}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            row = dict(record["row"])
            if record["table"] == "documents":
                row["published_at"] = datetime.fromisoformat(row["published_at"])
            rows[record["table"]].append(row)
    with engine.begin() as conn:
        for table in CORPUS_TABLES:
            if rows[table]:
                conn.execute(insert(schema.metadata.tables[table]), rows[table])
    return sorted({r["chunker_version"] for r in rows["chunks"]})


class CassetteEmbedder:
    """Replays recorded query vectors, looked up by the prefixed query text."""

    def __init__(self, vectors: Mapping[str, list[float]]) -> None:
        self._vectors = vectors

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vectors[t] for t in texts]


class CassetteReranker:
    """Replays recorded rerank scores by (query, chunk text); unscored chunks rank last."""

    def __init__(self, scores: Mapping[str, Mapping[str, float]], texts: Mapping[str, str]):
        self._scores = scores  # query -> chunk_id -> score
        self._ids = {text: cid for cid, text in texts.items()}

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        by_id = self._scores.get(query, {})
        return [by_id.get(self._ids.get(t, ""), -1e9) for t in texts]


@dataclass(frozen=True)
class Cassettes:
    cases: list[GoldenCase]
    vectors: dict[str, list[float]]  # prefixed query -> vector
    rerank: dict[str, dict[str, float]] | None  # query -> chunk_id -> score; None if missing


def read_cassettes(directory: Path, cases: Sequence[GoldenCase]) -> Cassettes:
    """Cases with a recorded query vector; rerank scores only when every such case has them."""
    from retrieval.embeddings import QUERY_PREFIX

    kept: list[GoldenCase] = []
    vectors: dict[str, list[float]] = {}
    rerank: dict[str, dict[str, float]] = {}
    complete = True
    for case in cases:
        query_file = directory / "queries" / f"{case.id}.json"
        if not query_file.is_file():
            continue
        kept.append(case)
        vectors[QUERY_PREFIX + case.question.strip()] = json.loads(query_file.read_text())["vector"]
        rerank_file = directory / "rerank" / f"{case.id}.json"
        if rerank_file.is_file():
            rerank[case.question] = json.loads(rerank_file.read_text())["scores"]
        else:
            complete = False
    return Cassettes(kept, vectors, rerank if complete and kept else None)


def chunk_texts(engine: Engine) -> dict[str, str]:
    with engine.connect() as conn:
        return {
            r.id: r.text for r in conn.execute(select(schema.chunks.c.id, schema.chunks.c.text))
        }


# ------------------------------------------------------------------------------------ sweep


async def run_cases(service: Any, cases: Sequence[GoldenCase]) -> list[int | None]:
    ranks = []
    for case in cases:
        hits = await service.search(case.question, case.filters, k=SEARCH_DEPTH)
        ranks.append(first_hit_rank(hits, case))
    return ranks


async def sweep(
    engine: Engine,
    cases: Sequence[GoldenCase],
    configs: Sequence[str],
    rerank_options: Sequence[bool],
    embedder: Any,
    reranker: Any,
    *,
    candidates: int = 50,
    rerank_top: int = 20,
) -> list[ConfigResult]:
    from retrieval.embeddings import QueryEmbedder
    from retrieval.search import SearchService

    results = []
    for version in configs:
        for rerank in rerank_options:
            service = SearchService(
                engine,
                QueryEmbedder(embedder, "eval"),
                chunker_version=version,
                candidates=candidates,
                reranker=reranker if rerank else None,
                rerank_top=rerank_top,
            )
            ranks = await run_cases(service, cases)
            results.append(summarise(version, rerank, cases, ranks))
    return results


def _parse_rerank(raw: str) -> list[bool]:
    values = {"on": True, "off": False}
    try:
        return [values[v.strip()] for v in raw.split(",") if v.strip()]
    except KeyError as exc:
        raise argparse.ArgumentTypeError("--rerank takes on, off or on,off") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.retrieval", description=__doc__)
    parser.add_argument("--configs", help="comma-separated chunker versions")
    parser.add_argument("--rerank", type=_parse_rerank, default=[False, True])
    parser.add_argument("--cassettes-only", action="store_true")
    parser.add_argument("--golden", type=Path, default=GOLDEN_PATH)
    parser.add_argument("--cassettes", type=Path, default=CASSETTES_DIR)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    parser.add_argument("--reports", type=Path, default=REPORTS_DIR)
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="with --cassettes-only: store this run as the baseline (default config, no rerank)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    from ingestion.config import IngestionSettings
    from ingestion.db import make_engine

    args = _parser().parse_args(argv)
    settings = IngestionSettings()
    engine = make_engine(settings.require_database_url())
    cases = load_golden(args.golden)
    try:
        if args.cassettes_only:
            versions = load_cassette_corpus(engine, args.cassettes / "corpus.jsonl")
            tape = read_cassettes(args.cassettes, cases)
            cases = tape.cases
            embedder: Any = CassetteEmbedder(tape.vectors)
            reranker: Any = (
                CassetteReranker(tape.rerank, chunk_texts(engine)) if tape.rerank else None
            )
            rerank_options = [r for r in args.rerank if not r or reranker is not None]
            corpus = "cassette"
        else:
            from ingestion.embed import SentenceTransformerEmbedder
            from retrieval.rerank import CrossEncoderReranker

            versions = [settings.retrieval_chunker_version]
            embedder = SentenceTransformerEmbedder(settings.embedding_model, prefix="")
            reranker = CrossEncoderReranker(settings.rerank_model) if True in args.rerank else None
            rerank_options = args.rerank
            corpus = "full"
        if args.configs:
            versions = [v.strip() for v in args.configs.split(",") if v.strip()]
        if not cases:
            raise RuntimeError("no golden cases to evaluate")
        results = asyncio.run(
            sweep(
                engine,
                cases,
                versions,
                rerank_options,
                embedder,
                reranker,
                candidates=settings.retrieval_candidates,
                rerank_top=settings.retrieval_rerank_top,
            )
        )
    finally:
        engine.dispose()
    report = build_report(results, corpus, datetime.now(UTC).date())
    path = write_report(report, args.reports)
    print(report_markdown(report))
    print(f"report: {path}")
    if args.cassettes_only and args.write_baseline:
        baseline = baseline_from(report, settings.retrieval_chunker_version)
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
        print(f"baseline: {args.baseline}")
    elif args.cassettes_only:
        if not args.baseline.is_file():
            print(f"missing baseline {args.baseline}", file=sys.stderr)
            return 1
        error = check_baseline(report, json.loads(args.baseline.read_text()))
        if error is not None:
            print(error, file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
