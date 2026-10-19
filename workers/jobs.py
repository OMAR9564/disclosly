"""arq jobs (contracts/cli.md): `ingest_company`, `embed_pending`, nightly ingestion.

One job at a time (`max_jobs = 1`) keeps the worker inside its 768 MB limit. The server embeds
only small batches; the laptop does bulk embedding (`python -m ingestion.embed`).
"""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings
from arq.worker import func
from sqlalchemy import Engine

from ingestion.adapters import build_adapters
from ingestion.config import IngestionSettings, load_companies
from ingestion.db import make_engine
from ingestion.embed import Embedder, SentenceTransformerEmbedder, embed_pending
from ingestion.pipeline import Pipeline, RunSummary, Trigger
from ingestion.run import DEFAULT_WINDOW_DAYS, parse_chunker_version, token_counter

logger = logging.getLogger("workers.jobs")

SERVER_EMBED_BATCH = 16
DEFAULT_EMBED_LIMIT = 500

Ctx = dict[str, Any]
# Runs one ingestion for the given tickers; replaced by fakes in tests.
IngestRunner = Callable[[list[str], Trigger], Awaitable[RunSummary]]


def default_ingest_runner(settings: IngestionSettings, engine: Engine) -> IngestRunner:
    companies = {c.ticker: c for c in load_companies().companies}

    async def run(tickers: list[str], trigger: Trigger) -> RunSummary:
        selected = [companies[t] for t in tickers]
        sources = {c.market for c in selected}
        only = "sec" if sources == {"us"} else "kap" if sources == {"bist"} else None
        async with AsyncExitStack() as stack:
            pipeline = Pipeline(
                engine,
                await build_adapters(settings, stack, only),
                data_dir=settings.ingest_data_dir,
                disk_limit_gb=settings.corpus_disk_limit_gb,
                chunker=parse_chunker_version(settings.retrieval_chunker_version),
                counter=token_counter(settings),
            )
            since = datetime.now(UTC) - timedelta(days=DEFAULT_WINDOW_DAYS)
            return await pipeline.run(selected, since, trigger=trigger)

    return run


async def startup(ctx: Ctx) -> None:
    settings = IngestionSettings()
    engine = make_engine(settings.require_database_url())
    ctx["settings"] = settings
    ctx["engine"] = engine
    ctx["ingest"] = default_ingest_runner(settings, engine)
    ctx["tickers"] = [c.ticker for c in load_companies().companies]
    ctx["embedder"] = None  # loaded on first use: the model costs memory


async def shutdown(ctx: Ctx) -> None:
    engine = ctx.get("engine")
    if engine is not None:
        engine.dispose()


def _embedder(ctx: Ctx) -> Embedder:
    if ctx.get("embedder") is None:
        ctx["embedder"] = SentenceTransformerEmbedder(ctx["settings"].embedding_model)
    embedder: Embedder = ctx["embedder"]
    return embedder


async def ingest_company(ctx: Ctx, ticker: str, trigger: Trigger = "manual") -> dict[str, Any]:
    ticker = ticker.strip().upper()
    if ticker not in ctx["tickers"]:
        raise ValueError(f"unknown ticker {ticker}")
    runner: IngestRunner = ctx["ingest"]
    summary = await runner([ticker], trigger)
    logger.info("ingest_company finished", extra={"ticker": ticker, **summary.as_dict()})
    return summary.as_dict()


async def embed_pending_job(ctx: Ctx, limit: int = DEFAULT_EMBED_LIMIT) -> int:
    settings: IngestionSettings = ctx["settings"]
    count = await asyncio.to_thread(
        embed_pending,
        ctx["engine"],
        _embedder(ctx),
        chunker_version=settings.retrieval_chunker_version,
        batch=SERVER_EMBED_BATCH,
        limit=limit,
    )
    logger.info("embed_pending finished", extra={"chunks": count})
    return count


async def nightly_ingest(ctx: Ctx) -> int:
    """Queue one `ingest_company` per configured ticker, then one embedding job."""
    redis = ctx["redis"]
    for ticker in ctx["tickers"]:
        await redis.enqueue_job("ingest_company", ticker, "schedule")
    await redis.enqueue_job("embed_pending")
    return len(ctx["tickers"])


class WorkerSettings:
    functions: ClassVar = [func(ingest_company), func(embed_pending_job, name="embed_pending")]
    cron_jobs: ClassVar = [cron(nightly_ingest, hour=2, minute=30)]
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 1
    job_timeout = 3 * 60 * 60
    # `arq --check` (container health check) reads this key; refresh it every minute.
    health_check_interval = 60
    redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379"))
