from array import array
from collections.abc import Sequence

import pytest

from ingestion.schema import EMBEDDING_DIM
from retrieval.embeddings import CACHE_TTL_S, QUERY_PREFIX, QueryEmbedder, cache_key, normalise


class FakeEmbedder:
    def __init__(self, dims: int = EMBEDDING_DIM) -> None:
        self.texts: list[str] = []
        self.dims = dims

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return [[0.5] * self.dims for _ in texts]


class FakeCache:
    def __init__(self, broken: bool = False) -> None:
        self.data: dict[str, bytes] = {}
        self.ttl: dict[str, int | None] = {}
        self.broken = broken

    async def get(self, name: str) -> bytes | None:
        if self.broken:
            raise ConnectionError("down")
        return self.data.get(name)

    async def set(self, name: str, value: bytes, ex: int | None = None) -> bool | None:
        if self.broken:
            raise ConnectionError("down")
        self.data[name] = value
        self.ttl[name] = ex
        return True


def test_key_uses_normalised_query() -> None:
    assert normalise("  Net   Borç ") == "net borç"
    assert cache_key("m", "Net  borç") == cache_key("m", "net borç")
    assert cache_key("m", "a").startswith("qemb:m:")
    assert cache_key("m", "a") != cache_key("other", "a")


async def test_cache_hit_avoids_embedder_call() -> None:
    fake, cache = FakeEmbedder(), FakeCache()
    embedder = QueryEmbedder(fake, "m", cache)

    first = await embedder.embed("Net borç")
    second = await embedder.embed("net  BORÇ")

    assert fake.texts == [QUERY_PREFIX + "Net borç"]
    assert first == second == [0.5] * EMBEDDING_DIM
    (key,) = cache.data
    assert len(cache.data[key]) == 4 * EMBEDDING_DIM  # float32 bytes
    assert list(array("f", cache.data[key])) == first
    assert cache.ttl[key] == CACHE_TTL_S == 7 * 24 * 3600


async def test_works_without_cache_or_with_broken_cache() -> None:
    fake = FakeEmbedder()
    assert len(await QueryEmbedder(fake, "m").embed("x")) == EMBEDDING_DIM
    assert len(await QueryEmbedder(fake, "m", FakeCache(broken=True)).embed("x")) == EMBEDDING_DIM
    assert len(fake.texts) == 2


async def test_wrong_cached_size_is_recomputed() -> None:
    fake, cache = FakeEmbedder(), FakeCache()
    cache.data[cache_key("m", "x")] = b"\x00" * 8
    await QueryEmbedder(fake, "m", cache).embed("x")
    assert fake.texts == [QUERY_PREFIX + "x"]


async def test_wrong_vector_size_is_rejected() -> None:
    with pytest.raises(ValueError, match="384"):
        await QueryEmbedder(FakeEmbedder(dims=3), "m").embed("x")
