"""Tests for api.search_client (search through the worker queue)."""

from datetime import UTC, datetime
from typing import Any

import pytest

from api.search_client import SEARCH_QUEUE, RemoteSearch
from ingestion.models import DocType
from retrieval.types import SearchFilters, SearchUnavailable

HIT = {
    "chunk_id": "c1",
    "text": "Net kâr 1.234,5 milyon TL",
    "kind": "text",
    "source": {
        "document_id": 7,
        "ticker": "THYAO",
        "doc_type": "kap_financial_report",
        "period": "2025-Q3",
        "section": "Gelir tablosu",
        "page": 4,
        "source_url": "https://www.kap.org.tr/x",
    },
    "score": 0.9,
    "vector_rank": 1,
    "fulltext_rank": None,
}
DOC = {
    "document_id": 7,
    "ticker": "THYAO",
    "doc_type": "kap_financial_report",
    "period": "2025-Q3",
    "published_at": datetime(2025, 11, 1, tzinfo=UTC).isoformat(),
    "language": "tr",
    "status": "ready",
    "source_url": "https://www.kap.org.tr/x",
}


class FakeJob:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.timeout: float | None = None

    async def result(self, timeout: float | None = None) -> Any:
        self.timeout = timeout
        if self._error is not None:
            raise self._error
        return self._result


class FakePool:
    def __init__(self, job: FakeJob) -> None:
        self.job = job
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.closed = False

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((function, args, kwargs))
        return self.job

    async def aclose(self) -> None:
        self.closed = True


async def test_search_enqueues_on_search_queue_and_parses_hits() -> None:
    pool = FakePool(FakeJob([HIT]))
    search = RemoteSearch("redis://unused", pool=pool, timeout=5)
    hits = await search.search("net kâr", SearchFilters(tickers=["thyao"]), 3)
    assert hits[0].chunk_id == "c1"
    assert hits[0].source.page == 4
    assert pool.calls == [
        ("search", ("net kâr", {"tickers": ["THYAO"]}, 3), {"_queue_name": SEARCH_QUEUE})
    ]
    assert pool.job.timeout == 5
    await search.aclose()
    assert pool.closed


async def test_list_documents_sends_plain_values() -> None:
    pool = FakePool(FakeJob([DOC]))
    docs = await RemoteSearch("redis://unused", pool=pool).list_documents(
        "THYAO", [DocType("kap_financial_report")], "2025-Q1", None
    )
    assert docs[0].document_id == 7
    assert pool.calls[0][1] == ("THYAO", ["kap_financial_report"], "2025-Q1", None)


@pytest.mark.parametrize("job", [FakeJob(error=TimeoutError()), FakeJob(result="nope")])
async def test_worker_failure_is_search_unavailable(job: FakeJob) -> None:
    with pytest.raises(SearchUnavailable):
        await RemoteSearch("redis://unused", pool=FakePool(job)).search("q")
