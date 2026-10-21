"""Redis tier of llm.cache and the router's cache use (T027)."""

import httpx

from llm.cache import REDIS_TTL_SECONDS, ResponseCache, redis_key
from llm.cache_key import cache_key
from llm.router import ModelRouter, cooldown_key
from llm.types import ChatMessage, ChatRequest, ChatResponse
from tests.unit.llm.fakes import FakeStore, config, http

REQUEST = ChatRequest(messages=[ChatMessage(role="user", content="hello")])
SCHEMA_REQUEST = REQUEST.model_copy(update={"response_schema": {"type": "object"}})


def _router(
    responses: dict[str, httpx.Response | Exception], hosts: list[str], redis: FakeStore
) -> ModelRouter:
    return ModelRouter(
        config("a", "b"),
        http(responses, hosts),
        store=FakeStore(),
        cache=ResponseCache(redis, None),
    )


async def test_identical_repeat_makes_no_provider_call() -> None:
    redis, hosts = FakeStore(), list[str]()
    router = _router({}, hosts, redis)

    first = await router.chat(REQUEST, task_class="cheap")
    second = await router.chat(REQUEST, task_class="cheap")

    assert hosts == ["a"]
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.content == first.content
    assert second.provider == "a"
    assert redis.ttls[redis_key(cache_key(REQUEST, "a-model"))] == REDIS_TTL_SECONDS


async def test_whitespace_variant_hits_the_cache() -> None:
    redis, hosts = FakeStore(), list[str]()
    router = _router({}, hosts, redis)
    await router.chat(REQUEST, task_class="cheap")

    variant = ChatRequest(messages=[ChatMessage(role="user", content="  hello \n")])
    assert (await router.chat(variant, task_class="cheap")).cache_hit is True
    assert hosts == ["a"]


async def test_cache_is_checked_per_provider_in_order() -> None:
    """A cached answer from the second provider is used even when the first is cooling down."""
    redis, hosts = FakeStore(), list[str]()
    cached = ChatResponse(content="from b", provider="b", model="b-model")
    await ResponseCache(redis, None).put(cache_key(REQUEST, "b-model"), cached)
    store = FakeStore()
    await store.set(cooldown_key("a"), "1", ex=60)
    router = ModelRouter(
        config("a", "b"), http({}, hosts), store=store, cache=ResponseCache(redis, None)
    )

    response = await router.chat(REQUEST, task_class="cheap")

    assert hosts == []
    assert response.content == "from b"
    assert response.cache_hit is True
    assert response.fallback_from == ["a"]


async def test_failures_are_not_cached() -> None:
    redis, hosts = FakeStore(), list[str]()
    router = _router({"a": httpx.Response(503)}, hosts, redis)

    await router.chat(REQUEST, task_class="cheap")

    assert redis_key(cache_key(REQUEST, "a-model")) not in redis.values
    assert redis_key(cache_key(REQUEST, "b-model")) in redis.values


async def test_schema_invalid_output_is_not_cached() -> None:
    redis, hosts = FakeStore(), list[str]()
    router = _router({}, hosts, redis)

    await router.chat(SCHEMA_REQUEST, task_class="cheap")  # content "hi" is not a JSON object

    assert not any(key.startswith("llm:cache:") for key in redis.values)


async def test_cached_entry_drops_per_call_fields() -> None:
    redis = FakeStore()
    response = ChatResponse(
        content="x", provider="a", model="m", latency_ms=12.0, fallback_from=["z"], cache_hit=True
    )
    await ResponseCache(redis, None).put("k", response)

    loaded = await ResponseCache(redis, None).get("k")

    assert loaded is not None
    assert (loaded.latency_ms, loaded.fallback_from, loaded.cache_hit) == (0.0, [], False)
