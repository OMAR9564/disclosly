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


# SEC tables state theirs in a header, "(In millions, except ...)", which a long table split
# into chunks leaves in an earlier chunk of the same section.
_TABLE_HEADER = re.compile(r"\((?:[Dd]ollars |[Aa]mounts )?[Ii]n (thousands|millions|billions)")
_HEADER_SCALES = {
    "thousands": Decimal(10) ** 3,
    "millions": Decimal(10) ** 6,
    "billions": Decimal(10) ** 9,
}
# How far back a split table's header is looked for.
HEADER_LOOKBACK = 3


def table_header_unit(text: str) -> tuple[Decimal, str] | None:
    """The last "(In millions" style header in `text`: it is the one nearest the rows below."""
    found = _TABLE_HEADER.findall(text)
    if not found:
        return None
    word = found[-1].lower()
    return _HEADER_SCALES[word], word


def _report_units(engine: Engine, document_ids: set[int]) -> dict[int, tuple[Decimal, str]]:
    chunks = schema.chunks
    query = (
        select(chunks.c.document_id, chunks.c.text)
        .where(chunks.c.document_id.in_(document_ids), chunks.c.text.contains("Sunum Para Birimi"))
        .order_by(chunks.c.document_id, chunks.c.ordinal)
    )
    units: dict[int, tuple[Decimal, str]] = {}
    with engine.connect() as conn:
        for document_id, text in conn.execute(query):
            if document_id not in units and (unit := presentation_unit(text)) is not None:
                units[document_id] = unit
    return units


def _header_units(engine: Engine, chunk_ids: set[str]) -> dict[str, tuple[Decimal, str]]:
    """For each chunk, the nearest table header in it or in the few chunks before it."""
    hit, before = schema.chunks.alias("hit"), schema.chunks.alias("before")
    query = (
        select(hit.c.id, before.c.text)
        .join(
            before,
            (before.c.document_id == hit.c.document_id)
            & (before.c.chunker_version == hit.c.chunker_version)
            & before.c.section_id.is_not_distinct_from(hit.c.section_id)
            & before.c.ordinal.between(hit.c.ordinal - HEADER_LOOKBACK, hit.c.ordinal),
        )
        .where(hit.c.id.in_(chunk_ids), before.c.text.regexp_match(_TABLE_HEADER.pattern))
        .order_by(hit.c.id, before.c.ordinal.desc())
    )
    units: dict[str, tuple[Decimal, str]] = {}
    with engine.connect() as conn:
        for chunk_id, text in conn.execute(query):
            if chunk_id not in units and (unit := table_header_unit(text)) is not None:
                units[chunk_id] = unit
    return units


def with_presentation_units(engine: Engine, items: list[SeenItem]) -> list[SeenItem]:
    """Attach each chunk's unit: the KAP report's presentation unit, else its table header."""
    chunk_items = [i for i in items if i.cite_id.startswith("c:")]
    document_ids = {i.source.get("document_id") for i in chunk_items} - {None}
    if not chunk_items:
        return items
    reports = _report_units(engine, document_ids) if document_ids else {}  # type: ignore[arg-type]
    headers = _header_units(engine, {i.cite_id[2:] for i in chunk_items})
    attached = []
    for item in items:
        unit = reports.get(item.source.get("document_id")) or headers.get(item.cite_id[2:])  # type: ignore[arg-type]
        attached.append(
            item.model_copy(update={"scale": unit[0], "unit_note": unit[1]}) if unit else item
        )
    return attached


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
