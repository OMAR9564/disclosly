"""Full-text path: `ts_rank_cd` top-N, query parsed with both Turkish and English configs."""

import re

from sqlalchemy import ColumnElement, Connection, func, literal_column, select

from ingestion import schema
from retrieval.filters import chunk_filters
from retrieval.types import SearchFilters

_WORD_RE = re.compile(r"\w+")
CONFIGS = ("tr_unaccent", "en_unaccent")


def _tsquery(query: str) -> ColumnElement[str] | None:
    """Every query word must match (AND), parsed with the Turkish or the English config.

    Matching any word (OR) let common words ("ne", "kaç", "company") pull in hundreds of
    unrelated chunks, which outranked good vector hits after fusion (golden set: TR recall@10
    0.59 with OR, 0.82 with AND, rerank on; see ADR 0002). Stop words are dropped by
    each config.
    """
    words = _WORD_RE.findall(query)
    if not words:
        return None
    joined = " ".join(words)
    parts = [
        func.websearch_to_tsquery(literal_column(f"'{config}'::regconfig"), joined)
        for config in CONFIGS
    ]
    # OR between configs: an English chunk only has English stems, a Turkish one Turkish stems.
    return parts[0].op("||")(parts[1])


def fulltext_search(
    conn: Connection,
    query: str,
    filters: SearchFilters | None,
    *,
    chunker_version: str,
    limit: int,
) -> list[str]:
    """Chunk IDs, best match first."""
    tsquery = _tsquery(query)
    if tsquery is None:
        return []
    chunks = schema.chunks
    rank = func.ts_rank_cd(chunks.c.tsv, tsquery)
    rows = conn.execute(
        select(chunks.c.id)
        .where(chunks.c.tsv.op("@@")(tsquery), chunk_filters(filters, chunker_version))
        .order_by(rank.desc(), chunks.c.id)
        .limit(limit)
    ).all()
    return [r.id for r in rows]
