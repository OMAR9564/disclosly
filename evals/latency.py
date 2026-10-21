"""Search latency (SC-005, quickstart §6): run the golden questions several times, report p95.

Runs against the full corpus with the real models and the configured defaults
(`RETRIEVAL_CHUNKER_VERSION`, `RETRIEVAL_RERANK`). The query embedding cache is not used, so
every search pays for the embedding:

    python -m evals.latency --rounds 3
"""

import argparse
import asyncio
import json
import math
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evals.retrieval import GOLDEN_PATH, load_golden
from retrieval.search import DEFAULT_K
from retrieval.types import SearchFilters


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


async def measure(
    service: Any, questions: Sequence[tuple[str, SearchFilters]], rounds: int
) -> list[float]:
    timings = []
    for _ in range(rounds):
        for query, filters in questions:
            started = time.perf_counter()
            await service.search(query, filters, k=DEFAULT_K)
            timings.append((time.perf_counter() - started) * 1000)
    return timings


def summary(timings: Sequence[float], rounds: int, rerank: bool) -> dict[str, Any]:
    return {
        "searches": len(timings),
        "rounds": rounds,
        "rerank": rerank,
        "p50_ms": round(percentile(timings, 50), 1),
        "p95_ms": round(percentile(timings, 95), 1),
        "max_ms": round(max(timings), 1),
    }


def main(argv: Sequence[str] | None = None) -> int:
    from ingestion.config import IngestionSettings
    from ingestion.db import make_engine
    from ingestion.embed import SentenceTransformerEmbedder
    from retrieval.embeddings import QueryEmbedder
    from retrieval.rerank import CrossEncoderReranker
    from retrieval.search import SearchService

    parser = argparse.ArgumentParser(prog="python -m evals.latency", description=__doc__)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--golden", type=Path, default=GOLDEN_PATH)
    args = parser.parse_args(argv)

    settings = IngestionSettings()
    engine = make_engine(settings.require_database_url())
    embedder = SentenceTransformerEmbedder(settings.embedding_model, prefix="")
    reranker = CrossEncoderReranker.from_settings(settings) if settings.retrieval_rerank else None
    service = SearchService(
        engine,
        QueryEmbedder(embedder, settings.embedding_model),
        chunker_version=settings.retrieval_chunker_version,
        candidates=settings.retrieval_candidates,
        reranker=reranker,
        rerank_top=settings.retrieval_rerank_top,
    )
    questions = [(case.question, case.filters) for case in load_golden(args.golden)]
    try:
        # One warm-up search loads the models; it is not timed.
        asyncio.run(service.search(questions[0][0], questions[0][1]))
        timings = asyncio.run(measure(service, questions, args.rounds))
    finally:
        engine.dispose()
    json.dump(summary(timings, args.rounds, reranker is not None), sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
