"""Round trip: api.search_client through Redis to workers.search (real Redis, fake models)."""

import asyncio
import os
from datetime import UTC, datetime
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import Worker

from api.search_client import SEARCH_QUEUE, RemoteSearch
from ingestion.models import DocType
from retrieval.types import DocumentMeta, SearchFilters, SearchHit, SourceRef
from workers import search as search_worker

HIT = SearchHit(
    chunk_id="c1",
    text="Net kâr 1.234,5",
    kind="text",
    source=SourceRef(
        document_id=7,
        ticker="THYAO",
        doc_type=DocType.KAP_FINANCIAL_REPORT,
        period="2025-Q3",
        section="Gelir tablosu",
        page=4,
        source_url="https://www.kap.org.tr/x",
    ),
    score=0.9,
    vector_rank=1,
    fulltext_rank=None,
)
DOC = DocumentMeta(
    document_id=7,
    ticker="THYAO",
    doc_type=DocType.KAP_FINANCIAL_REPORT,
    period="2025-Q3",
    published_at=datetime(2025, 11, 1, tzinfo=UTC),
    language="tr",
    status="ready",
    source_url="https://www.kap.org.tr/x",
)


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def search(self, query: str, filters: SearchFilters | None, k: int) -> list[SearchHit]:
        self.calls.append(("search", query, filters, k))
        return [HIT]

    async def list_documents(
        self,
        ticker: str,
        doc_types: list[DocType] | None,
        period_from: str | None,
        period_to: str | None,
    ) -> list[DocumentMeta]:
        self.calls.append(("list", ticker, doc_types, period_from, period_to))
        return [DOC]


async def test_jobs_return_json_ready_rows() -> None:
    service = FakeService()
    ctx: dict[str, Any] = {"search": service}
    rows = await search_worker.search_job(ctx, "net kâr", {"tickers": ["thyao"]}, 2)
    assert rows == [HIT.model_dump(mode="json")]
    assert service.calls[0] == ("search", "net kâr", SearchFilters(tickers=["THYAO"]), 2)
    docs = await search_worker.list_documents_job(ctx, "THYAO", ["kap_financial_report"])
    assert docs[0]["published_at"].startswith("2025-11-01")
    assert service.calls[1][2] == [DocType.KAP_FINANCIAL_REPORT]


async def test_round_trip_through_redis() -> None:
    """The API client and the worker agree on queue, job names and payloads."""
    settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379"))
    pool = await create_pool(settings)
    worker = Worker(
        functions=search_worker.SearchWorkerSettings.functions,
        queue_name=SEARCH_QUEUE,
        redis_pool=pool,
        burst=True,
        poll_delay=0.05,
        ctx={"search": FakeService()},
    )
    client = RemoteSearch("redis://unused", pool=pool, timeout=10)
    try:
        hits, docs, _ = await asyncio.gather(
            client.search("net kâr", SearchFilters(tickers=["THYAO"])),
            client.list_documents("THYAO"),
            _run_after_enqueue(worker),
        )
    finally:
        await pool.aclose()
    assert hits == [HIT]
    assert docs == [DOC]


async def _run_after_enqueue(worker: Worker) -> None:
    await asyncio.sleep(0.2)
    await worker.async_run()
