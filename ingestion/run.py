"""Ingestion commands (contracts/cli.md): `ingest`, `status`, `rechunk`.

Summaries go to stdout as JSON; logs go to stderr as JSON lines. Exit 0 on success, 1 on
failure.
"""

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, func, select, text

from api.logging import JsonFormatter, RedactionFilter
from ingestion import schema
from ingestion.adapters import build_adapters
from ingestion.chunk import ChunkerConfig, HuggingFaceCounter, TokenCounter, WhitespaceCounter
from ingestion.config import Company, IngestionSettings, load_companies
from ingestion.db import database_size_bytes, make_engine
from ingestion.pipeline import Pipeline, SourceAdapter

logger = logging.getLogger("ingestion.run")

DEFAULT_WINDOW_DAYS = 730


def configure_cli_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def parse_chunker_version(version: str) -> ChunkerConfig:
    """`v1-600-64` -> ChunkerConfig(600, 64)."""
    try:
        _, size, overlap = version.split("-")
        return ChunkerConfig(int(size), int(overlap))
    except ValueError as exc:
        raise ValueError(f"invalid chunker version {version!r}") from exc


def token_counter(settings: IngestionSettings) -> TokenCounter:
    try:
        return HuggingFaceCounter(settings.embedding_model)
    except ImportError:
        logger.warning("transformers not installed; counting whitespace tokens")
        return WhitespaceCounter()


def select_companies(companies: Sequence[Company], tickers: str | None) -> list[Company]:
    if not tickers:
        return list(companies)
    wanted = {t.strip().upper() for t in tickers.split(",") if t.strip()}
    known = {c.ticker for c in companies}
    unknown = sorted(wanted - known)
    if unknown:
        raise ValueError(f"unknown tickers: {', '.join(unknown)}")
    return [c for c in companies if c.ticker in wanted]


def status_report(engine: Engine) -> dict[str, Any]:
    docs, runs = schema.documents, schema.ingestion_runs
    with engine.connect() as conn:
        counts: dict[str, int] = dict(
            conn.execute(select(docs.c.status, func.count()).group_by(docs.c.status)).all()
        )
        last = conn.execute(select(runs).order_by(runs.c.id.desc()).limit(1)).first()
        missing_meta = conn.execute(
            text(
                "SELECT count(*) FROM chunks WHERE ticker IS NULL OR period IS NULL "
                "OR doc_type IS NULL OR section_id IS NULL OR language IS NULL"
            )
        ).scalar_one()
        missing_source = conn.execute(
            text(
                "SELECT count(*) FROM financial_facts "
                "WHERE source_document_id IS NULL OR source_ref IS NULL OR source_ref = ''"
            )
        ).scalar_one()
        chunks = conn.execute(select(func.count()).select_from(schema.chunks)).scalar_one()
        unembedded = conn.execute(
            select(func.count())
            .select_from(schema.chunks)
            .where(schema.chunks.c.embedding.is_(None))
        ).scalar_one()
    return {
        "documents": {str(k): int(v) for k, v in counts.items()},
        "chunks": int(chunks),
        "chunks_without_embedding": int(unembedded),
        "chunks_missing_metadata": int(missing_meta),
        "facts_missing_source": int(missing_source),
        "disk_bytes": database_size_bytes(engine),
        "last_run": None
        if last is None
        else {
            "id": last.id,
            "started_at": last.started_at.isoformat(),
            "finished_at": last.finished_at.isoformat() if last.finished_at else None,
            "trigger": last.trigger,
            "fetched": last.fetched,
            "skipped": last.skipped,
            "failed": last.failed,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ingestion.run")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest", help="fetch, parse, chunk, extract facts, clean up")
    ingest.add_argument("--tickers", help="comma-separated, default all configured")
    ingest.add_argument("--source", choices=["kap", "sec"])
    ingest.add_argument("--since", type=lambda v: datetime.fromisoformat(v).replace(tzinfo=UTC))
    ingest.add_argument("--dry-run", action="store_true")
    status = sub.add_parser("status", help="counts per status, last run, disk usage")
    status.add_argument("--json", action="store_true")
    rechunk = sub.add_parser("rechunk", help="build chunks for another chunker version")
    rechunk.add_argument("--size", type=int, required=True)
    rechunk.add_argument("--overlap", type=int, default=64)
    return parser


async def _with_pipeline(
    settings: IngestionSettings, engine: Engine, source: str | None, action: Any
) -> Any:
    async with AsyncExitStack() as stack:
        adapters: list[SourceAdapter] = await build_adapters(settings, stack, source)
        pipeline = Pipeline(
            engine,
            adapters,
            data_dir=settings.ingest_data_dir,
            disk_limit_gb=settings.corpus_disk_limit_gb,
            chunker=parse_chunker_version(settings.retrieval_chunker_version),
            counter=token_counter(settings),
        )
        return await action(pipeline)


def main(argv: Sequence[str] | None = None) -> int:
    configure_cli_logging()
    args = _parser().parse_args(argv)
    try:
        settings = IngestionSettings()
        engine = make_engine(settings.require_database_url())
        if args.command == "status":
            report = status_report(engine)
            print(json.dumps(report, indent=None if args.json else 2))
            return 0
        if args.command == "ingest":
            companies = select_companies(load_companies().companies, args.tickers)
            since = args.since or datetime.now(UTC) - timedelta(days=DEFAULT_WINDOW_DAYS)
            summary = asyncio.run(
                _with_pipeline(
                    settings,
                    engine,
                    args.source,
                    lambda p: p.run(companies, since, dry_run=args.dry_run),
                )
            )
            out = summary.as_dict()
            if args.dry_run:
                out["listed"] = summary.listed
            print(json.dumps(out))
            return 0
        config = ChunkerConfig(args.size, args.overlap)
        result = asyncio.run(_with_pipeline(settings, engine, None, lambda p: p.rechunk(config)))
        print(json.dumps(result.as_dict()))
        return 0 if result.failed == 0 else 1
    except Exception as exc:
        logger.error("command failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
