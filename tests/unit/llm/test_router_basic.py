"""Tests for the basic provider walk in llm.router and the FakeRouter (T009)."""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from llm.config import Price, Provider, RouterConfig
from llm.router import ModelRouter
from llm.testing import CassetteMiss, FakeRouter, cassette_key, interaction
from llm.types import ChatMessage, ChatRequest, ChatResponse, ProvidersUnavailable


def _provider(name: str) -> Provider:
    return Provider(
        name=name,
        base_url=f"https://{name}.test/v1",
        model=f"{name}-model",
        api_key=SecretStr("k"),
        daily_requests=10,
        native_tools=True,
        price_per_mtok=Price(input=0, output=0),
    )


CONFIG = RouterConfig(
    classes={"cheap": [_provider("a"), _provider("b"), _provider("c")], "strong": []},
    skipped=[],
)
REQUEST = ChatRequest(messages=[ChatMessage(role="user", content="hello")])
OK = {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}


def _router(statuses: dict[str, int]) -> tuple[ModelRouter, list[str]]:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host.split(".")[0]
        hosts.append(host)
        status = statuses.get(host, 200)
        return httpx.Response(status, json=OK if status == 200 else {})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ModelRouter(CONFIG, http), hosts


async def test_first_provider_answers() -> None:
    router, hosts = _router({})
    response = await router.chat(REQUEST, task_class="cheap")

    assert response.provider == "a"
    assert response.fallback_from == []
    assert hosts == ["a"]


async def test_walks_to_next_provider_on_failure() -> None:
    router, hosts = _router({"a": 429, "b": 503})
    response = await router.chat(REQUEST, task_class="cheap")

    assert response.provider == "c"
    assert response.fallback_from == ["a", "b"]
    assert hosts == ["a", "b", "c"]


async def test_all_fail_raises_providers_unavailable() -> None:
    router, _ = _router({"a": 500, "b": 401, "c": 429})
    with pytest.raises(ProvidersUnavailable) as excinfo:
        await router.chat(REQUEST, task_class="cheap")
    assert excinfo.value.attempted == ["a", "b", "c"]
    assert excinfo.value.task_class == "cheap"


async def test_empty_class_raises_providers_unavailable() -> None:
    router, hosts = _router({})
    with pytest.raises(ProvidersUnavailable):
        await router.chat(REQUEST, task_class="strong")
    assert hosts == []


async def test_fake_router_replays_and_records_calls() -> None:
    fake = FakeRouter()
    recorded = ChatResponse(content="hi", provider="a", model="m")
    fake.add(REQUEST, "cheap", recorded)

    response = await fake.chat(REQUEST, task_class="cheap")

    assert response == recorded
    assert fake.calls == [("cheap", REQUEST)]


async def test_fake_router_unknown_request_raises() -> None:
    fake = FakeRouter()
    fake.add(REQUEST, "cheap", ChatResponse(content="hi", provider="a", model="m"))

    with pytest.raises(CassetteMiss):
        await fake.chat(REQUEST, task_class="strong")
    other = ChatRequest(messages=[ChatMessage(role="user", content="different")])
    with pytest.raises(CassetteMiss):
        await fake.chat(other, task_class="cheap")


async def test_fake_router_replays_repeated_requests_in_order() -> None:
    fake = FakeRouter()
    first = ChatResponse(content="first", provider="a", model="m")
    second = ChatResponse(content="second", provider="a", model="m")
    fake.load_interactions(
        [interaction(REQUEST, "strong", first), interaction(REQUEST, "strong", second)]
    )

    replies = [await fake.chat(REQUEST, task_class="strong") for _ in range(3)]

    assert [r.content for r in replies] == ["first", "second", "second"]


async def test_fake_router_loads_cassette(tmp_path: Path) -> None:
    response = ChatResponse(content="hi", provider="groq-llama", model="m", tokens_in=3)
    path = tmp_path / "case.json"
    path.write_text(json.dumps({"interactions": [interaction(REQUEST, "cheap", response)]}))

    fake = FakeRouter.from_cassette(path)

    assert await fake.chat(REQUEST, task_class="cheap") == response
    assert cassette_key(REQUEST, "cheap") != cassette_key(REQUEST, "strong")
