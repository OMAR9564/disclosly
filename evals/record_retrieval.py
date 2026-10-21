"""Record retrieval cassettes (contracts/golden-set.md), on the laptop with the `ml` group.

Reads the real corpus through `DATABASE_URL` (SSH tunnel), then writes into
`evals/cassettes/retrieval/`: `corpus.jsonl` (the documents behind the golden cases, at most
`--max-documents`, with their chunks and vectors), `queries/<id>.json` and `rerank/<id>.json`.

    python -m evals.record_retrieval --max-documents 10
    python -m evals.retrieval --cassettes-only --write-baseline   # on an empty database
"""

import argparse
import json
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, and_, select
from sqlalchemy.orm import aliased

from evals.retrieval import CASSETTES_DIR, GOLDEN_PATH, GoldenCase, load_golden, normalise
from ingestion import schema
from ingestion.embed import Embedder
from retrieval.embeddings import QUERY_PREFIX
from retrieval.filters import chunk_filters
from retrieval.rerank import Reranker

DEFAULT_MAX_DOCUMENTS = 10


def interleave(cases: Sequence[GoldenCase]) -> list[GoldenCase]:
    """Alternate languages (tr, en, tr, ...) so a small fixture corpus covers both."""
    by_language: dict[str, list[GoldenCase]] = {}
    for case in cases:
        by_language.setdefault(case.language, []).append(case)
    queues = [by_language[k] for k in sorted(by_language, reverse=True)]  # "tr" first
    out: list[GoldenCase] = []
    while any(queues):
        for queue in queues:
            if queue:
                out.append(queue.pop(0))
    return out


def pick_documents(engine: Engine, cases: Sequence[GoldenCase], limit: int) -> list[int]:
    """Current documents named by the cases' expected evidence, languages interleaved, at most
    `limit`."""
    docs, companies = schema.documents, schema.companies
    newer = aliased(docs)
    picked: list[int] = []
    with engine.connect() as conn:
        for case in interleave(cases):
            for e in case.expected:
                candidates = conn.execute(
                    select(docs.c.id)
                    .join(companies, companies.c.id == docs.c.company_id)
                    .outerjoin(newer, newer.c.supersedes_id == docs.c.id)
                    .where(
                        companies.c.ticker == e.ticker,
                        docs.c.doc_type == e.doc_type.value,
                        docs.c.period == e.period,
                        newer.c.id.is_(None),
                    )
                    .order_by(docs.c.published_at.desc())
                ).scalars()
                # A quarter has many material events: take the one that holds the quote.
                quote = normalise(e.quote)
                for document_id in candidates:
                    texts = conn.execute(
                        select(schema.chunks.c.text).where(
                            schema.chunks.c.document_id == document_id
                        )
                    ).scalars()
                    if any(quote in normalise(t) for t in texts):
                        if document_id not in picked:
                            picked.append(int(document_id))
                        break
                if len(picked) >= limit:
                    return picked
    return picked


def _plain(row: Any, skip: frozenset[str] = frozenset()) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row._mapping.items():
        if key in skip:
            continue
        if key == "published_at":
            value = value.isoformat()
        elif key == "embedding" and value is not None:
            # 4 decimals keep the cassette small; replay uses the stored values as they are.
            value = [round(float(v), 4) for v in value]
        out[key] = value
    return out


def export_corpus(
    engine: Engine, document_ids: Sequence[int], chunker_version: str
) -> Iterator[dict[str, Any]]:
    """`{"table", "row"}` records for the documents, their company, sections and chunks."""
    docs, companies, sections, chunks = (
        schema.documents,
        schema.companies,
        schema.sections,
        schema.chunks,
    )
    ids = list(document_ids)
    with engine.connect() as conn:
        doc_rows = conn.execute(select(docs).where(docs.c.id.in_(ids)).order_by(docs.c.id)).all()
        company_ids = sorted({r.company_id for r in doc_rows})
        for r in conn.execute(select(companies).where(companies.c.id.in_(company_ids))):
            yield {"table": "companies", "row": _plain(r)}
        for r in doc_rows:
            # The fixture corpus has no older versions, so the link is dropped.
            yield {"table": "documents", "row": {**_plain(r), "supersedes_id": None}}
        for r in conn.execute(
            select(sections).where(sections.c.document_id.in_(ids)).order_by(sections.c.id)
        ):
            yield {"table": "sections", "row": _plain(r)}
        query = (
            select(chunks)
            .where(chunks.c.document_id.in_(ids), chunks.c.chunker_version == chunker_version)
            .order_by(chunks.c.id)
        )
        for r in conn.execute(query):
            yield {"table": "chunks", "row": _plain(r, frozenset({"tsv"}))}


def record_query(embedder: Embedder, model: str, case: GoldenCase) -> dict[str, Any]:
    (vector,) = embedder.embed([QUERY_PREFIX + case.question.strip()])
    return {"model": model, "vector": [float(v) for v in vector]}


def record_rerank(
    engine: Engine,
    reranker: Reranker,
    model: str,
    case: GoldenCase,
    document_ids: Sequence[int],
    chunker_version: str,
) -> dict[str, Any]:
    """Scores for every fixture chunk the case's filters allow (all possible candidates)."""
    chunks = schema.chunks
    with engine.connect() as conn:
        rows = conn.execute(
            select(chunks.c.id, chunks.c.text).where(
                and_(
                    chunks.c.document_id.in_(list(document_ids)),
                    chunk_filters(case.filters, chunker_version),
                )
            )
        ).all()
    scores = reranker.score(case.question, [r.text for r in rows]) if rows else []
    return {"model": model, "scores": {r.id: s for r, s in zip(rows, scores, strict=True)}}


def record(
    engine: Engine,
    cases: Sequence[GoldenCase],
    out: Path,
    *,
    embedder: Embedder,
    reranker: Reranker,
    embedding_model: str,
    rerank_model: str,
    chunker_version: str,
    max_documents: int = DEFAULT_MAX_DOCUMENTS,
) -> list[str]:
    """Write all cassettes; returns the IDs of the cases whose documents are in the corpus."""
    document_ids = pick_documents(engine, cases, max_documents)
    out.mkdir(parents=True, exist_ok=True)
    records = list(export_corpus(engine, document_ids, chunker_version))
    with (out / "corpus.jsonl").open("w", encoding="utf-8") as fh:
        for item in records:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    chunk_rows = [r["row"] for r in records if r["table"] == "chunks"]

    def covered(case: GoldenCase) -> bool:
        return any(
            (c["ticker"], c["doc_type"], c["period"]) == (e.ticker, e.doc_type.value, e.period)
            and normalise(e.quote) in normalise(c["text"])
            for e in case.expected
            for c in chunk_rows
        )

    recorded = []
    for case in cases:
        if not covered(case):
            continue
        for folder, payload in (
            ("queries", record_query(embedder, embedding_model, case)),
            (
                "rerank",
                record_rerank(engine, reranker, rerank_model, case, document_ids, chunker_version),
            ),
        ):
            (out / folder).mkdir(exist_ok=True)
            (out / folder / f"{case.id}.json").write_text(json.dumps(payload) + "\n")
        recorded.append(case.id)
    return recorded


def main(argv: Sequence[str] | None = None) -> int:
    from ingestion.config import IngestionSettings
    from ingestion.db import make_engine
    from ingestion.embed import SentenceTransformerEmbedder
    from retrieval.rerank import CrossEncoderReranker

    parser = argparse.ArgumentParser(prog="python -m evals.record_retrieval", description=__doc__)
    parser.add_argument("--golden", type=Path, default=GOLDEN_PATH)
    parser.add_argument("--out", type=Path, default=CASSETTES_DIR)
    parser.add_argument("--max-documents", type=int, default=DEFAULT_MAX_DOCUMENTS)
    parser.add_argument("--chunker-version")
    args = parser.parse_args(argv)
    settings = IngestionSettings()
    engine = make_engine(settings.require_database_url())
    try:
        recorded = record(
            engine,
            load_golden(args.golden),
            args.out,
            embedder=SentenceTransformerEmbedder(settings.embedding_model, prefix=""),
            reranker=CrossEncoderReranker.from_settings(settings),
            embedding_model=settings.embedding_model,
            rerank_model=settings.rerank_model,
            chunker_version=args.chunker_version or settings.retrieval_chunker_version,
            max_documents=args.max_documents,
        )
    finally:
        engine.dispose()
    print(json.dumps({"recorded_cases": len(recorded)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
