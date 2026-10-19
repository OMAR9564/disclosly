"""Vector path: HNSW cosine top-N inside the filters (research §R6)."""

from collections.abc import Sequence

from sqlalchemy import Connection, select, text

from ingestion import schema
from retrieval.filters import chunk_filters
from retrieval.types import SearchFilters

EF_SEARCH = 80


def vector_search(
    conn: Connection,
    embedding: Sequence[float],
    filters: SearchFilters | None,
    *,
    chunker_version: str,
    limit: int,
) -> list[str]:
    """Chunk IDs, nearest first. Call inside a transaction: the HNSW settings are `SET LOCAL`."""
    chunks = schema.chunks
    # Iterative scans keep going when a selective filter removes most HNSW candidates.
    conn.execute(text(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}"))
    conn.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    distance = chunks.c.embedding.cosine_distance(list(embedding))
    rows = conn.execute(
        select(chunks.c.id, distance.label("distance"))
        .where(chunks.c.embedding.is_not(None), chunk_filters(filters, chunker_version))
        .order_by(distance)
        .limit(limit)
    ).all()
    # relaxed_order may return rows slightly out of order; sort them again.
    return [r.id for r in sorted(rows, key=lambda r: (r.distance, r.id))]
