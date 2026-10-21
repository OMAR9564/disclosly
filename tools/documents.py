"""Document tools over `retrieval.search` (contracts/agent-answer.md).

`search_documents` returns citable chunk items (`cite_id = "c:<chunk_id>"`);
`list_documents` returns document metadata, which is not citable.
"""

import re
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import Engine, select

from ingestion import schema
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


# KAP financial reports state the unit of every table once: "Sunum Para Birimi: 1.000.000 TL".
_PRESENTATION = re.compile(r"Sunum Para Birimi:\s*((?:\d{1,3}(?:\.\d{3})*\s*)?[A-Z]{2,3})")


def presentation_unit(text: str) -> tuple[Decimal, str] | None:
    """`(scale, note)` from a "Sunum Para Birimi" line: "1.000.000 TL" is (10^6, "1.000.000 TL")."""
    match = _PRESENTATION.search(text)
    if match is None:
        return None
    note = " ".join(match.group(1).split())
    digits = note.split(" ")[0].replace(".", "") if note[0].isdigit() else "1"
    return Decimal(digits), note


def with_presentation_units(engine: Engine, items: list[SeenItem]) -> list[SeenItem]:
    """Attach each chunk's report unit, read from the first chunk of its document that states it."""
    ids = {item.source.get("document_id") for item in items} - {None}
    if not ids:
        return items
    chunks = schema.chunks
    query = (
        select(chunks.c.document_id, chunks.c.text)
        .where(chunks.c.document_id.in_(ids), chunks.c.text.contains("Sunum Para Birimi"))
        .order_by(chunks.c.document_id, chunks.c.ordinal)
    )
    units: dict[int, tuple[Decimal, str]] = {}
    with engine.connect() as conn:
        for document_id, text in conn.execute(query):
            if document_id not in units and (unit := presentation_unit(text)) is not None:
                units[document_id] = unit
    return [
        item.model_copy(update={"scale": unit[0], "unit_note": unit[1]})
        if (unit := units.get(item.source.get("document_id"))) is not None  # type: ignore[arg-type]
        else item
        for item in items
    ]


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
