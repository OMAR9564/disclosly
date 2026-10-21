from collections.abc import Sequence
from typing import Any

import pytest

from ingestion.config import IngestionSettings
from ingestion.pipeline import RunSummary, Trigger
from workers import jobs


class FakeRedis:
    def __init__(self) -> None:
        self.jobs: list[tuple[Any, ...]] = []

    async def enqueue_job(self, name: str, *args: Any) -> None:
        self.jobs.append((name, *args))


def _ctx() -> dict[str, Any]:
    calls: list[tuple[list[str], Trigger]] = []

    async def ingest(tickers: list[str], trigger: Trigger) -> RunSummary:
        calls.append((tickers, trigger))
        return RunSummary(run_id=1, fetched=3)

    return {
        "settings": IngestionSettings(),
        "engine": object(),
        "ingest": ingest,
        "ingest_calls": calls,
        "tickers": ["AAPL", "THYAO"],
        "embedder": None,
        "redis": FakeRedis(),
    }


async def test_ingest_company_runs_one_ticker() -> None:
    ctx = _ctx()
    result = await jobs.ingest_company(ctx, " thyao ")
    assert result["fetched"] == 3
    assert ctx["ingest_calls"] == [(["THYAO"], "manual")]


async def test_ingest_company_rejects_unknown_ticker() -> None:
    with pytest.raises(ValueError, match="unknown ticker"):
        await jobs.ingest_company(_ctx(), "NOPE")


async def test_embed_pending_uses_small_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_embed(engine: object, embedder: object, **kwargs: Any) -> int:
        seen.update(kwargs, embedder=embedder)
        return 7

    class FakeEmbedder:
        def __init__(self, name: str) -> None:
            self.name = name

        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            return []

    monkeypatch.setattr(jobs, "embed_pending", fake_embed)
    monkeypatch.setattr(jobs, "SentenceTransformerEmbedder", FakeEmbedder)
    ctx = _ctx()
    assert await jobs.embed_pending_job(ctx, limit=100) == 7
    assert seen["batch"] == jobs.SERVER_EMBED_BATCH and seen["limit"] == 100
    assert seen["chunker_version"] == "v1-600-64"
    first = ctx["embedder"]
    await jobs.embed_pending_job(ctx)
    assert ctx["embedder"] is first  # model loaded once


async def test_nightly_queues_every_ticker_then_embedding() -> None:
    ctx = _ctx()
    assert await jobs.nightly_ingest(ctx) == 2
    assert ctx["redis"].jobs == [
        ("ingest_company", "AAPL", "schedule"),
        ("ingest_company", "THYAO", "schedule"),
        ("embed_pending",),
    ]


async def test_startup_and_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    disposed: list[bool] = []

    class FakeEngine:
        def dispose(self) -> None:
            disposed.append(True)

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setattr(jobs, "make_engine", lambda url: FakeEngine())
    ctx: dict[str, Any] = {}
    await jobs.startup(ctx)
    assert len(ctx["tickers"]) == 40 and ctx["embedder"] is None
    await jobs.shutdown(ctx)
    assert disposed == [True]
    await jobs.shutdown({})


def test_worker_settings() -> None:
    settings = jobs.WorkerSettings
    assert settings.max_jobs == 1
    assert {f.name for f in settings.functions} == {"ingest_company", "embed_pending"}
    assert settings.cron_jobs[0].hour == 2


async def test_cleanup_llm_cache_deletes_stale_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []

    def fake_delete(engine: object) -> int:
        seen.append(engine)
        return 4

    monkeypatch.setattr(jobs, "delete_stale", fake_delete)
    ctx = _ctx()
    assert await jobs.cleanup_llm_cache(ctx) == 4
    assert seen == [ctx["engine"]]


def test_cleanup_runs_nightly() -> None:
    (cleanup,) = [c for c in jobs.WorkerSettings.cron_jobs if c.name.endswith("cleanup_llm_cache")]
    assert cleanup.hour == 3
