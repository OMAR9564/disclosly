"""US2 acceptance scenarios for `search_documents` / `list_documents` on the fixture corpus."""

import logging
import os
from collections.abc import AsyncIterator, Sequence

import pytest
import redis.asyncio
from sqlalchemy import Engine

from ingestion.models import DocType
from retrieval import search as search_module
from retrieval.embeddings import QUERY_PREFIX, QueryEmbedder
from retrieval.rerank import Reranker
from retrieval.search import SearchFilters, SearchService
from tests.integration.retrieval.corpus import VERSION, Corpus, basis

pytestmark = pytest.mark.integration

# Query text (with the e5 prefix) to the axis of the chunks it means.
MEANINGS = {
    "THYAO net borç 2025": 0,
    "fleet expansion aircraft": 2,  # no literal match: the uçak siparişi chunk is on axis 2
    "favok": 1,
    "net debt": 0,
    "revenue": 5,
}


class AxisEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [basis(MEANINGS.get(t.removeprefix(QUERY_PREFIX), 7)) for t in texts]


class ReverseReranker:
    """Prefers the chunk that fusion ranked last."""

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        return [float(i) for i in range(len(texts))]


def service(engine: Engine, reranker: Reranker | None = None) -> SearchService:
    return SearchService(
        engine, QueryEmbedder(AxisEmbedder(), "fake"), chunker_version=VERSION, reranker=reranker
    )


async def test_ticker_filter_returns_only_that_company(corpus: Corpus) -> None:
    hits = await service(corpus.engine).search(
        "THYAO net borç 2025", SearchFilters(tickers=["THYAO"])
    )
    assert hits and {h.source.ticker for h in hits} == {"THYAO"}
    top = hits[0]
    assert top.chunk_id == corpus.ids["thy-debt"]
    assert (top.vector_rank, top.fulltext_rank) == (1, 1)
    assert top.source.doc_type is DocType.KAP_FINANCIAL_REPORT
    assert (top.source.period, top.source.section, top.source.page) == ("2025-Q3", "body", 3)
    assert top.source.source_url == "https://example.test/doc/10"
    assert top.source.document_id == 10
    scores = [(-h.score, h.chunk_id) for h in hits]
    assert scores == sorted(scores)


async def test_meaning_and_keyword_matches_are_merged(corpus: Corpus) -> None:
    hits = await service(corpus.engine).search("fleet expansion aircraft", k=10)
    by_name = dict(zip(corpus.names([h.chunk_id for h in hits]), hits, strict=True))
    semantic = by_name["thy-order"]
    assert semantic.vector_rank == 1 and semantic.fulltext_rank is None
    assert hits[0].chunk_id == corpus.ids["thy-order"]

    mixed = await service(corpus.engine).search("revenue", k=10)
    names = corpus.names([h.chunk_id for h in mixed])
    assert names[0] == "aapl-q"  # in both rankings
    assert "aapl-raw" in names  # keyword only: not embedded yet
    raw = mixed[names.index("aapl-raw")]
    assert raw.vector_rank is None and raw.fulltext_rank is not None
    assert raw.kind == "text"


async def test_at_most_k_results(corpus: Corpus) -> None:
    svc = service(corpus.engine)
    assert len(await svc.search("revenue")) <= 6
    assert len(await svc.search("revenue", k=1)) == 1
    assert len(await svc.search("revenue", k=50)) <= 50


async def test_no_match_filter_returns_empty(corpus: Corpus) -> None:
    svc = service(corpus.engine)
    assert await svc.search("net borç", SearchFilters(tickers=["NOPE"])) == []
    assert await svc.search("net", SearchFilters(period_from="2030-Q1")) == []


@pytest.mark.parametrize("k", [0, 51])
async def test_invalid_arguments(corpus: Corpus, k: int) -> None:
    svc = service(corpus.engine)
    with pytest.raises(ValueError, match="k must"):
        await svc.search("net", k=k)
    with pytest.raises(ValueError, match="empty"):
        await svc.search("   ")


async def test_rerank_reorders_candidates(corpus: Corpus) -> None:
    plain = await service(corpus.engine).search("revenue", k=3)
    reranked = await service(corpus.engine, ReverseReranker()).search("revenue", k=3)
    assert len(reranked) == 3
    assert reranked[0].chunk_id != plain[0].chunk_id
    assert [h.score for h in reranked] == sorted((h.score for h in reranked), reverse=True)


async def test_search_logs_latency(corpus: Corpus, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="retrieval.search"):
        await service(corpus.engine).search("favok", SearchFilters(language="tr"), k=2)
    (record,) = [r for r in caplog.records if r.getMessage() == "search"]
    assert record.latency_ms >= 0  # type: ignore[attr-defined]
    assert record.filters == {"language": "tr"}  # type: ignore[attr-defined]


class FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}

    def __enter__(self) -> "FakeSpan":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


class FakeTracer:
    def __init__(self) -> None:
        self.spans: list[tuple[str, FakeSpan]] = []

    def start_as_current_span(self, name: str) -> FakeSpan:
        span = FakeSpan()
        self.spans.append((name, span))
        return span


async def test_search_emits_span(corpus: Corpus, monkeypatch: pytest.MonkeyPatch) -> None:
    tracer = FakeTracer()
    monkeypatch.setattr(search_module, "tracer", tracer)
    await service(corpus.engine).search("favok", k=2)
    ((name, span),) = tracer.spans
    assert name == "search_documents"
    assert span.attributes == {"search.k": 2, "search.rerank": False, "search.hits": 2}


@pytest.fixture
async def redis_client() -> AsyncIterator[redis.asyncio.Redis]:
    client = redis.asyncio.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def test_query_vector_cached_in_redis(
    corpus: Corpus, redis_client: redis.asyncio.Redis
) -> None:
    embedder = AxisEmbedder()
    svc = SearchService(
        corpus.engine,
        QueryEmbedder(embedder, "fake", redis_client),  # type: ignore[arg-type]
        chunker_version=VERSION,
    )
    first = await svc.search("net debt")
    second = await svc.search("Net  Debt")
    assert embedder.calls == 1
    assert [h.chunk_id for h in first][:2] == [h.chunk_id for h in second][:2]
    (key,) = await redis_client.keys("qemb:fake:*")
    assert 0 < await redis_client.ttl(key) <= 7 * 24 * 3600


async def test_list_documents(corpus: Corpus) -> None:
    svc = service(corpus.engine)
    docs = await svc.list_documents("thyao")
    assert [d.document_id for d in docs] == [10, 11, 12]
    assert {d.ticker for d in docs} == {"THYAO"}
    filtered = await svc.list_documents(
        "AAPL", doc_types=[DocType.TEN_K], period_from="2024-Q1", period_to="2024-FY"
    )
    assert [(d.document_id, d.period) for d in filtered] == [(21, "2024-FY")]
    assert await svc.list_documents("NOPE") == []
    with pytest.raises(ValueError):
        await svc.list_documents("AAPL", period_from="bad")


async def test_module_functions_use_default_service(
    corpus: Corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(search_module, "_default", service(corpus.engine))
    hits = await search_module.search_documents("favok", k=1)
    assert [h.chunk_id for h in hits] == [corpus.ids["thy-favok"]]
    assert [d.document_id for d in await search_module.list_documents("AAPL")] == [21, 22]
