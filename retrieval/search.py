"""Hybrid search (contracts/search.md): vector + full-text, RRF fusion, optional rerank.

Both paths run in Postgres with the same filters, so no hit can violate them (FR-015).
"""

import asyncio
import logging
import os
import time
from collections.abc import Sequence
from typing import Any, cast

from opentelemetry import trace
from sqlalchemy import Engine, exists, select
from sqlalchemy.orm import aliased

from ingestion import schema
from ingestion.config import IngestionSettings
from ingestion.db import make_engine
from ingestion.models import DocType
from retrieval.embeddings import Cache, QueryEmbedder
from retrieval.filters import period_range
from retrieval.fulltext import fulltext_search
from retrieval.fusion import rrf
from retrieval.rerank import Reranker, rerank
from retrieval.types import DocumentMeta, SearchFilters, SearchHit, SourceRef
from retrieval.vector import vector_search

__all__ = [
    "DocumentMeta",
    "SearchFilters",
    "SearchHit",
    "SearchService",
    "SourceRef",
    "list_documents",
    "search_documents",
]

logger = logging.getLogger("retrieval.search")
tracer = trace.get_tracer("retrieval.search")

DEFAULT_K = 6
MAX_K = 50


class SearchService:
    def __init__(
        self,
        engine: Engine,
        query_embedder: QueryEmbedder,
        *,
        chunker_version: str,
        candidates: int = 50,
        reranker: Reranker | None = None,
        rerank_top: int = 20,
    ) -> None:
        self._engine = engine
        self._query_embedder = query_embedder
        self._chunker_version = chunker_version
        self._candidates = candidates
        self._reranker = reranker
        self._rerank_top = rerank_top

    async def search(
        self, query: str, filters: SearchFilters | None = None, k: int = DEFAULT_K
    ) -> list[SearchHit]:
        if not query.strip():
            raise ValueError("query must not be empty")
        if not 1 <= k <= MAX_K:
            raise ValueError(f"k must be between 1 and {MAX_K}")
        started = time.perf_counter()
        with tracer.start_as_current_span("search_documents") as span:
            span.set_attribute("search.k", k)
            span.set_attribute("search.rerank", self._reranker is not None)
            embedding = await self._query_embedder.embed(query)
            hits = await asyncio.to_thread(self._search_sync, query, embedding, filters, k)
            span.set_attribute("search.hits", len(hits))
        logger.info(
            "search",
            extra={
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "k": k,
                "hits": len(hits),
                "rerank": self._reranker is not None,
                "filters": (filters or SearchFilters()).model_dump(exclude_none=True, mode="json"),
            },
        )
        return hits

    def _search_sync(
        self, query: str, embedding: Sequence[float], filters: SearchFilters | None, k: int
    ) -> list[SearchHit]:
        options: dict[str, Any] = {"chunker_version": self._chunker_version}
        with self._engine.begin() as conn:
            by_vector = vector_search(conn, embedding, filters, limit=self._candidates, **options)
            by_text = fulltext_search(conn, query, filters, limit=self._candidates, **options)
        fused = rrf([by_vector, by_text])
        if not fused:
            return []
        keep = max(k, self._rerank_top) if self._reranker is not None else k
        top = fused[:keep]
        rows = self._load([cid for cid, _ in top])
        if self._reranker is not None:
            scored = rerank(
                self._reranker, query, [(cid, rows[cid]["text"]) for cid, _ in top], len(top)
            )
        else:
            scored = top
        vector_rank = {cid: i for i, cid in enumerate(by_vector, start=1)}
        text_rank = {cid: i for i, cid in enumerate(by_text, start=1)}
        return [
            SearchHit(
                chunk_id=cid,
                text=rows[cid]["text"],
                kind=rows[cid]["kind"],
                source=SourceRef(
                    document_id=rows[cid]["document_id"],
                    ticker=rows[cid]["ticker"],
                    doc_type=DocType(rows[cid]["doc_type"]),
                    period=rows[cid]["period"],
                    section=rows[cid]["section"] or "body",
                    page=rows[cid]["page"],
                    source_url=rows[cid]["source_url"],
                ),
                score=score,
                vector_rank=vector_rank.get(cid),
                fulltext_rank=text_rank.get(cid),
            )
            for cid, score in scored[:k]
        ]

    def _load(self, chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
        chunks, docs, sections = schema.chunks, schema.documents, schema.sections
        query = (
            select(
                chunks.c.id,
                chunks.c.text,
                chunks.c.kind,
                chunks.c.document_id,
                chunks.c.ticker,
                chunks.c.doc_type,
                chunks.c.period,
                chunks.c.page,
                sections.c.label.label("section"),
                docs.c.source_url,
            )
            .select_from(
                chunks.join(docs, docs.c.id == chunks.c.document_id).outerjoin(
                    sections, sections.c.id == chunks.c.section_id
                )
            )
            .where(chunks.c.id.in_(chunk_ids))
        )
        with self._engine.connect() as conn:
            return {r.id: dict(r._mapping) for r in conn.execute(query)}

    async def list_documents(
        self,
        ticker: str,
        doc_types: list[DocType] | None = None,
        period_from: str | None = None,
        period_to: str | None = None,
    ) -> list[DocumentMeta]:
        """Current (not superseded) documents of a company, newest first."""
        SearchFilters(period_from=period_from, period_to=period_to)  # validates the periods
        return await asyncio.to_thread(
            self._list_sync, ticker.strip().upper(), doc_types, period_from, period_to
        )

    def _list_sync(
        self,
        ticker: str,
        doc_types: list[DocType] | None,
        period_from: str | None,
        period_to: str | None,
    ) -> list[DocumentMeta]:
        docs, companies = schema.documents, schema.companies
        newer = aliased(docs)
        query = (
            select(
                docs.c.id.label("document_id"),
                companies.c.ticker,
                docs.c.doc_type,
                docs.c.period,
                docs.c.published_at,
                docs.c.language,
                docs.c.status,
                docs.c.source_url,
            )
            .select_from(docs.join(companies, companies.c.id == docs.c.company_id))
            .where(
                companies.c.ticker == ticker,
                ~exists().where(newer.c.supersedes_id == docs.c.id),
                *period_range(docs.c.period, period_from, period_to),
            )
            .order_by(docs.c.published_at.desc(), docs.c.id)
        )
        if doc_types is not None:
            query = query.where(docs.c.doc_type.in_([d.value for d in doc_types]))
        with self._engine.connect() as conn:
            return [DocumentMeta.model_validate(dict(r._mapping)) for r in conn.execute(query)]


_default: SearchService | None = None


def default_service() -> SearchService:
    """Service from the environment: real models, Redis cache at `REDIS_URL` when set."""
    global _default
    if _default is None:
        import redis.asyncio

        from ingestion.embed import SentenceTransformerEmbedder
        from retrieval.rerank import CrossEncoderReranker

        settings = IngestionSettings()
        redis_url = os.environ.get("REDIS_URL")
        cache = cast(Cache, redis.asyncio.from_url(redis_url)) if redis_url else None
        _default = SearchService(
            make_engine(settings.require_database_url()),
            QueryEmbedder(
                SentenceTransformerEmbedder(settings.embedding_model, prefix=""),
                settings.embedding_model,
                cache,
            ),
            chunker_version=settings.retrieval_chunker_version,
            candidates=settings.retrieval_candidates,
            reranker=CrossEncoderReranker.from_settings(settings)
            if settings.retrieval_rerank
            else None,
            rerank_top=settings.retrieval_rerank_top,
        )
    return _default


async def search_documents(
    query: str, filters: SearchFilters | None = None, k: int = DEFAULT_K
) -> list[SearchHit]:
    return await default_service().search(query, filters, k)


async def list_documents(
    ticker: str,
    doc_types: list[DocType] | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
) -> list[DocumentMeta]:
    return await default_service().list_documents(ticker, doc_types, period_from, period_to)
