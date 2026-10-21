"""Document tools over `retrieval.search` (contracts/agent-answer.md).

`search_documents` returns citable chunk items (`cite_id = "c:<chunk_id>"`);
`list_documents` returns document metadata, which is not citable.
"""

from typing import Any, Protocol

from ingestion.models import DocType
from retrieval.types import DocumentMeta, SearchFilters, SearchHit
from tools.types import SeenItem

MAX_K = 8
DEFAULT_K = 6


class SearchBackend(Protocol):
    async def search(
        self, query: str, filters: SearchFilters | None = None, k: int = DEFAULT_K
    ) -> list[SearchHit]: ...

    async def list_documents(
        self,
        ticker: str,
        doc_types: list[DocType] | None = None,
        period_from: str | None = None,
        period_to: str | None = None,
    ) -> list[DocumentMeta]: ...


def chunk_item(hit: SearchHit) -> SeenItem:
    source = hit.source
    return SeenItem(
        cite_id=f"c:{hit.chunk_id}",
        text=hit.text,
        source={
            "kind": "chunk",
            "document_id": source.document_id,
            "ticker": source.ticker,
            "doc_type": source.doc_type.value,
            "period": source.period,
            "section": source.section,
            "page": source.page,
            "source_url": source.source_url,
        },
    )


async def search_documents(
    backend: SearchBackend,
    *,
    query: str,
    ticker: str | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
    doc_type: str | None = None,
    language: str | None = None,
    k: int = DEFAULT_K,
) -> list[SeenItem]:
    if not 1 <= k <= MAX_K:
        raise ValueError(f"k must be between 1 and {MAX_K}")
    filters = SearchFilters.model_validate(
        {
            "tickers": [ticker] if ticker else None,
            "period_from": period_from,
            "period_to": period_to,
            "doc_types": [doc_type] if doc_type else None,
            "language": language,
        }
    )
    return [chunk_item(hit) for hit in await backend.search(query, filters, k)]


async def list_documents(
    backend: SearchBackend,
    *,
    ticker: str,
    doc_type: str | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
) -> list[dict[str, Any]]:
    docs = await backend.list_documents(
        ticker, [DocType(doc_type)] if doc_type else None, period_from, period_to
    )
    return [d.model_dump(mode="json", exclude={"status"}) for d in docs]
