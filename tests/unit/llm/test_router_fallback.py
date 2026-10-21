"""Cooldown and failure classification in llm.router (T025, contracts/model-router.md)."""

import logging

import httpx
import pytest

from llm.router import ModelRouter, cooldown_key
from llm.types import ChatMessage, ChatRequest, ProvidersUnavailable
from tests.unit.llm.fakes import FakeStore, config, http

REQUEST = ChatRequest(messages=[ChatMessage(role="user", content="hello")])
EMPTY = {"choices": [{"message": {"role": "assistant", "content": ""}}]}
BAD_ARGS = {"choices": [{"message": {"tool_calls": [
    {"id": "1", "function": {"name": "f", "arguments": "{not json"}}]}}]}  # fmt: skip


@pytest.mark.parametrize(
    ("failure", "cooldown"),
    [
        (httpx.Response(429, headers={"Retry-After": "12"}), 12),
        (httpx.Response(429), 60),
        (httpx.Response(500), 60),
        (httpx.Response(503), 60),
        (httpx.ReadTimeout("slow"), 60),
        (httpx.ConnectError("refused"), 60),
        (httpx.Response(200, json=EMPTY), 30),
        (httpx.Response(200, json=BAD_ARGS), 30),
        (httpx.Response(400), 3600),
        (httpx.Response(401), 3600),
        (httpx.Response(403), 3600),
        (httpx.Response(404), 3600),
    ],
)
async def test_failure_sets_cooldown_and_falls_back(
    failure: httpx.Response | Exception, cooldown: int
) -> None:
    store, hosts = FakeStore(), list[str]()
    router = ModelRouter(config("a", "b"), http({"a": failure}, hosts), store=store)

    response = await router.chat(REQUEST, task_class="cheap")

    assert response.provider == "b"
    assert response.fallback_from == ["a"]
    assert store.ttls[cooldown_key("a")] == cooldown
    assert cooldown_key("b") not in store.values


async def test_request_too_large_falls_back_without_cooldown() -> None:
    store = FakeStore()
    router = ModelRouter(config("a", "b"), http({"a": httpx.Response(413)}, []), store=store)

    response = await router.chat(REQUEST, task_class="cheap")

    assert response.provider == "b"
    assert response.fallback_from == ["a"]
    assert cooldown_key("a") not in store.values


async def test_config_error_is_logged_as_error(caplog: pytest.LogCaptureFixture) -> None:
    router = ModelRouter(config("a", "b"), http({"a": httpx.Response(401)}, []), store=FakeStore())
    with caplog.at_level(logging.ERROR, logger="llm.router"):
        await router.chat(REQUEST, task_class="cheap")
    assert any(r.message == "provider config error" for r in caplog.records)


async def test_cooling_provider_is_skipped_without_a_call() -> None:
    store, hosts = FakeStore(), list[str]()
    await store.set(cooldown_key("a"), "1", ex=60)
    router = ModelRouter(config("a", "b"), http({}, hosts), store=store)

    response = await router.chat(REQUEST, task_class="cheap")

    assert hosts == ["b"]
    assert response.fallback_from == ["a"]


async def test_cooldown_is_respected_on_the_next_request() -> None:
    store, hosts = FakeStore(), list[str]()
    router = ModelRouter(config("a", "b"), http({"a": httpx.Response(503)}, hosts), store=store)

    await router.chat(REQUEST, task_class="cheap")
    await router.chat(REQUEST, task_class="cheap")

    assert hosts == ["a", "b", "b"]


async def test_all_failed_or_skipped_raises() -> None:
    store, hosts = FakeStore(), list[str]()
    await store.set(cooldown_key("a"), "1", ex=60)
    router = ModelRouter(config("a", "b"), http({"b": httpx.Response(500)}, hosts), store=store)

    with pytest.raises(ProvidersUnavailable) as excinfo:
        await router.chat(REQUEST, task_class="cheap")

    assert excinfo.value.attempted == ["a", "b"]
    assert hosts == ["b"]
