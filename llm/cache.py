"""Response cache (contracts/model-router.md, "Cache key"; research §R3).

Two tiers: Redis `llm:cache:{key}` with a 24 h TTL, then the Postgres `llm_cache` table, which
survives a Redis flush. A Postgres hit increments `hits`, updates `last_hit_at` and refills
Redis. Rows not hit for 30 days are removed by the nightly worker job (`delete_stale`).
"""

import asyncio
from typing import Any, Protocol

from sqlalchemy import Engine, text

from llm.types import ChatResponse

REDIS_TTL_SECONDS = 24 * 60 * 60
STALE_DAYS = 30

_HIT_SQL = text(
    "UPDATE llm_cache SET hits = hits + 1, last_hit_at = now() WHERE key = :key RETURNING response"
)
_PUT_SQL = text(
    "INSERT INTO llm_cache (key, provider, model, response)"
    " VALUES (:key, :provider, :model, CAST(:response AS JSONB))"
    " ON CONFLICT (key) DO NOTHING"
)
_STALE_SQL = text("DELETE FROM llm_cache WHERE last_hit_at < now() - make_interval(days => :days)")


class CacheRedis(Protocol):
    async def get(self, name: str) -> Any: ...

    async def set(self, name: str, value: str, ex: int | None = None) -> Any: ...


def redis_key(key: str) -> str:
    return f"llm:cache:{key}"


def _dump(response: ChatResponse) -> str:
    # Per-call fields are not part of a cached answer.
    return response.model_dump_json(exclude={"cache_hit", "fallback_from", "latency_ms"})


class ResponseCache:
    def __init__(self, redis: CacheRedis, engine: Engine | None) -> None:
        self._redis = redis
        self._engine = engine

    async def get(self, key: str) -> ChatResponse | None:
        raw = await self._redis.get(redis_key(key))
        if raw is not None:
            if self._engine is not None:
                await asyncio.to_thread(self._hit, key)
            return ChatResponse.model_validate_json(raw)
        if self._engine is None:
            return None
        stored = await asyncio.to_thread(self._hit, key)
        if stored is None:
            return None
        response = ChatResponse.model_validate(stored)
        await self._redis.set(redis_key(key), _dump(response), ex=REDIS_TTL_SECONDS)
        return response

    async def put(self, key: str, response: ChatResponse) -> None:
        payload = _dump(response)
        await self._redis.set(redis_key(key), payload, ex=REDIS_TTL_SECONDS)
        if self._engine is not None:
            await asyncio.to_thread(self._put, key, response, payload)

    def _hit(self, key: str) -> dict[str, Any] | None:
        assert self._engine is not None  # noqa: S101
        with self._engine.begin() as conn:
            row = conn.execute(_HIT_SQL, {"key": key}).first()
        return None if row is None else dict(row[0])

    def _put(self, key: str, response: ChatResponse, payload: str) -> None:
        assert self._engine is not None  # noqa: S101
        with self._engine.begin() as conn:
            conn.execute(
                _PUT_SQL,
                {
                    "key": key,
                    "provider": response.provider,
                    "model": response.model,
                    "response": payload,
                },
            )


def delete_stale(engine: Engine, days: int = STALE_DAYS) -> int:
    """Delete cache rows not hit for `days` days; returns the number removed."""
    with engine.begin() as conn:
        return conn.execute(_STALE_SQL, {"days": days}).rowcount
