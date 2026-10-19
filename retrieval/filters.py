"""SQL `WHERE` clauses shared by the vector and full-text paths (FR-015: filter before rank)."""

from sqlalchemy import ColumnElement, Integer, and_, case, cast, exists, func, select, true, tuple_
from sqlalchemy.orm import aliased

from ingestion import schema
from ingestion.models import Period
from retrieval.types import SearchFilters

chunks = schema.chunks


def _period_key(column: ColumnElement[str]) -> ColumnElement[tuple[int, int]]:
    """(year, rank) like `Period`: Q1-Q4 are 1-4, FY is 5."""
    year = cast(func.substr(column, 1, 4), Integer)
    rank = case((column.like("%-FY"), 5), else_=cast(func.substr(column, 7, 1), Integer))
    return tuple_(year, rank)


def period_range(
    column: ColumnElement[str], period_from: str | None, period_to: str | None
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if period_from is not None:
        start = Period.parse(period_from)
        conditions.append(_period_key(column) >= tuple_(start.year, start._rank))
    if period_to is not None:
        end = Period.parse(period_to)
        conditions.append(_period_key(column) <= tuple_(end.year, end._rank))
    return conditions


def chunk_filters(filters: SearchFilters | None, chunker_version: str) -> ColumnElement[bool]:
    """Conditions on `chunks`: the filters, the chunker version and no superseded documents."""
    filters = filters or SearchFilters()
    newer = aliased(schema.documents)
    conditions: list[ColumnElement[bool]] = [
        chunks.c.chunker_version == chunker_version,
        ~exists().where(newer.c.supersedes_id == chunks.c.document_id),
    ]
    if filters.tickers is not None:
        conditions.append(chunks.c.ticker.in_(filters.tickers))
    if filters.doc_types is not None:
        conditions.append(chunks.c.doc_type.in_([d.value for d in filters.doc_types]))
    if filters.language is not None:
        conditions.append(chunks.c.language == filters.language)
    if filters.sections is not None:
        sections = schema.sections
        conditions.append(
            chunks.c.section_id.in_(
                select(sections.c.id).where(sections.c.label.in_(filters.sections))
            )
        )
    conditions.extend(period_range(chunks.c.period, filters.period_from, filters.period_to))
    return and_(true(), *conditions)
