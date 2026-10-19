"""Query embedding with a Redis cache (research §R7, FR-016).

Key `qemb:{model}:{sha256(normalised query)}`, value float32 bytes, TTL 7 days. Cache errors
are logged and the vector is computed; search keeps working without Redis.
"""

import asyncio
import hashlib
import logging
from array import array
from typing import Protocol

from ingestion.embed import Embedder
from ingestion.schema import EMBEDDING_DIM

logger = logging.getLogger("retrieval.embeddings")

QUERY_PREFIX = "query: "
CACHE_TTL_S = 7 * 24 * 60 * 60


class Cache(Protocol):
    async def get(self, name: str) -> bytes | None: ...

    async def set(self, name: str, value: bytes, ex: int | None = None) -> bool | None: ...


def normalise(query: str) -> str:
    return " ".join(query.split()).casefold()


def cache_key(model: str, query: str) -> str:
    digest = hashlib.sha256(normalise(query).encode()).hexdigest()
    return f"qemb:{model}:{digest}"


class QueryEmbedder:
    def __init__(self, embedder: Embedder, model: str, cache: Cache | None = None) -> None:
        self._embedder = embedder
        self._model = model
        self._cache = cache

    async def embed(self, query: str) -> list[float]:
        key = cache_key(self._model, query)
        cached = await self._get(key)
        if cached is not None and len(cached) == 4 * EMBEDDING_DIM:
            return list(array("f", cached))
        vectors = await asyncio.to_thread(self._embedder.embed, [QUERY_PREFIX + query.strip()])
        vector = vectors[0]
        if len(vector) != EMBEDDING_DIM:
            raise ValueError(f"embedder must return {EMBEDDING_DIM} dims")
        await self._set(key, array("f", vector).tobytes())
        return list(vector)

    async def _get(self, key: str) -> bytes | None:
        if self._cache is None:
            return None
        try:
            return await self._cache.get(key)
        except Exception as exc:
            logger.warning("query cache read failed", extra={"error": type(exc).__name__})
            return None

    async def _set(self, key: str, value: bytes) -> None:
        if self._cache is None:
            return
        try:
            await self._cache.set(key, value, ex=CACHE_TTL_S)
        except Exception as exc:
            logger.warning("query cache write failed", extra={"error": type(exc).__name__})
