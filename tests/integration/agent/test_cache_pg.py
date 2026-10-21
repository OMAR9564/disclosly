"""Postgres tier of llm.cache (T027): a hit survives a Redis flush."""

from typing import Any

from sqlalchemy import Engine, text

from llm.cache import ResponseCache, delete_stale, redis_key
from llm.types import ChatResponse
from tests.unit.llm.fakes import FakeStore

KEY = "a" * 64
RESPONSE = ChatResponse(content="cached", provider="groq-llama", model="m", tokens_in=3)


def _row(engine: Engine) -> Any:
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT hits, last_hit_at, response FROM llm_cache WHERE key = :k"), {"k": KEY}
        ).first()


async def test_hit_survives_redis_flush(engine: Engine) -> None:
    await ResponseCache(FakeStore(), engine).put(KEY, RESPONSE)
    assert _row(engine).hits == 0

    flushed = FakeStore()  # a fresh Redis: nothing cached there
    loaded = await ResponseCache(flushed, engine).get(KEY)

    assert loaded == RESPONSE
    row = _row(engine)
    assert row.hits == 1
    assert redis_key(KEY) in flushed.values  # refilled


async def test_redis_hit_also_counts(engine: Engine) -> None:
    cache = ResponseCache(FakeStore(), engine)
    await cache.put(KEY, RESPONSE)
    await cache.get(KEY)
    await cache.get(KEY)
    assert _row(engine).hits == 2


async def test_miss_returns_none(engine: Engine) -> None:
    assert await ResponseCache(FakeStore(), engine).get(KEY) is None


async def test_delete_stale_removes_old_rows(engine: Engine) -> None:
    await ResponseCache(FakeStore(), engine).put(KEY, RESPONSE)
    await ResponseCache(FakeStore(), engine).put("b" * 64, RESPONSE)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE llm_cache SET last_hit_at = now() - interval '31 days' WHERE key = :k"),
            {"k": KEY},
        )
    assert delete_stale(engine) == 1
    assert _row(engine) is None
