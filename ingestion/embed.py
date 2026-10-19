"""Embed chunks that have no vector yet (research §R5).

Bulk runs on the laptop (`uv sync --group ml`) against the server database through an SSH
tunnel; the worker runs small batches for new documents. Passages get the `passage: ` prefix
that multilingual-e5 expects.

    python -m ingestion.embed --batch 64 --limit 2000 --chunker-version v1-600-64
"""

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from typing import Protocol

from sqlalchemy import Engine, exists, select, update

from ingestion import schema
from ingestion.config import IngestionSettings
from ingestion.db import make_engine
from ingestion.models import DocumentStatus
from ingestion.schema import EMBEDDING_DIM

logger = logging.getLogger("ingestion.embed")

PASSAGE_PREFIX = "passage: "
DEFAULT_BATCH = 64


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    """Local CPU model; needs the `ml` dependency group."""

    def __init__(self, model_name: str, prefix: str = PASSAGE_PREFIX) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name, device="cpu")
        self._prefix = prefix

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(
            [self._prefix + t for t in texts], normalize_embeddings=True, convert_to_numpy=True
        )
        return [list(map(float, v)) for v in vectors]


def embed_pending(
    engine: Engine,
    embedder: Embedder,
    *,
    chunker_version: str | None = None,
    batch: int = DEFAULT_BATCH,
    limit: int | None = None,
) -> int:
    """Embed up to `limit` chunks with a null embedding; returns how many were embedded."""
    if batch < 1:
        raise ValueError("batch must be >= 1")
    chunks = schema.chunks
    done = 0
    touched: set[int] = set()
    while limit is None or done < limit:
        size = batch if limit is None else min(batch, limit - done)
        query = select(chunks.c.id, chunks.c.document_id, chunks.c.text).where(
            chunks.c.embedding.is_(None)
        )
        if chunker_version is not None:
            query = query.where(chunks.c.chunker_version == chunker_version)
        with engine.connect() as conn:
            rows = conn.execute(query.order_by(chunks.c.id).limit(size)).all()
        if not rows:
            break
        vectors = embedder.embed([r.text for r in rows])
        if len(vectors) != len(rows) or any(len(v) != EMBEDDING_DIM for v in vectors):
            raise ValueError(f"embedder must return {len(rows)} vectors of {EMBEDDING_DIM} dims")
        with engine.begin() as conn:
            for row, vector in zip(rows, vectors, strict=True):
                conn.execute(update(chunks).where(chunks.c.id == row.id).values(embedding=vector))
        done += len(rows)
        touched.update(int(r.document_id) for r in rows)
        logger.info("embedded batch", extra={"chunks": len(rows), "total": done})
    mark_embedded(engine, touched)
    return done


def mark_embedded(engine: Engine, document_ids: set[int]) -> None:
    """Set `embedded` on chunked documents whose chunks all have vectors."""
    if not document_ids:
        return
    docs, chunks = schema.documents, schema.chunks
    pending = exists().where(chunks.c.document_id == docs.c.id, chunks.c.embedding.is_(None))
    with engine.begin() as conn:
        conn.execute(
            update(docs)
            .where(
                docs.c.id.in_(document_ids),
                docs.c.status == DocumentStatus.CHUNKED.value,
                ~pending,
            )
            .values(status=DocumentStatus.EMBEDDED.value)
        )


def main(argv: Sequence[str] | None = None) -> int:
    from ingestion.run import configure_cli_logging

    configure_cli_logging()
    parser = argparse.ArgumentParser(prog="python -m ingestion.embed")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--chunker-version")
    args = parser.parse_args(argv)
    try:
        settings = IngestionSettings()
        engine = make_engine(settings.require_database_url())
        embedder = SentenceTransformerEmbedder(settings.embedding_model)
        count = embed_pending(
            engine,
            embedder,
            chunker_version=args.chunker_version,
            batch=args.batch,
            limit=args.limit,
        )
    except Exception as exc:
        logger.error("embedding failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        return 1
    print(json.dumps({"embedded": count}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
