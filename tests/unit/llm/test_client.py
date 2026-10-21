"""Tests for llm.client with httpx.MockTransport fixtures per provider shape (T008)."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from llm.client import (
    InvalidResponse,
    ProviderConfigRejected,
    RateLimited,
    Unavailable,
    build_payload,
    chat_completion,
)
from llm.config import Price, Provider
from llm.types import ChatMessage, ChatRequest, ToolCall, ToolSchema

SEARCH = ToolSchema(
    name="search_documents",
    description="Search the corpus.",
    parameters={"type": "object", "properties": {"query": {"type": "string"}}},
)


def _provider(native_tools: bool = True, name: str = "groq-llama") -> Provider:
    return Provider(
        name=name,
        base_url="https://llm.example.test/v1",
        model="model-x",
        api_key=SecretStr("sk-test-key"),
        daily_requests=100,
        native_tools=native_tools,
        price_per_mtok=Price(input=0.1, output=0.2),
    )


def _request(**kwargs: Any) -> ChatRequest:
    return ChatRequest(messages=[ChatMessage(role="user", content="THYAO 2025 net kâr?")], **kwargs)


def _completion(message: dict[str, Any], usage: dict[str, int] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"choices": [{"index": 0, "message": message}]}
    if usage is not None:
        body["usage"] = usage
    return body


Handler = Callable[[httpx.Request], httpx.Response]


async def _call(
    handler: Handler, provider: Provider | None = None, request: ChatRequest | None = None
) -> Any:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return await chat_completion(http, provider or _provider(), request or _request())


# Response shapes seen from each provider family.
GEMINI_TEXT = _completion(
    {"role": "assistant", "content": "Net kâr 1.234,5 milyon TL."},
    {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
)
GROQ_TOOL = _completion(
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_abc",
                "type": "function",
                "function": {"name": "search_documents", "arguments": '{"query": "net kar"}'},
            }
        ],
    },
    {"prompt_tokens": 30, "completion_tokens": 5},
)
GITHUB_TOOL_DICT_ARGS = _completion(
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "t1", "function": {"name": "search_documents", "arguments": {"query": "x"}}}
        ],
    }
)
OPENROUTER_NO_USAGE = _completion({"role": "assistant", "content": "ok"})
CLOUDFLARE_JSON_MODE = _completion(
    {
        "role": "assistant",
        "content": json.dumps(
            {
                "tool_calls": [{"name": "search_documents", "arguments": {"query": "y"}}],
                "content": None,
            }
        ),
    }
)


async def test_text_answer_with_usage() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=GEMINI_TEXT)

    response = await _call(handler)

    assert response.content == "Net kâr 1.234,5 milyon TL."
    assert response.tool_calls == []
    assert (response.tokens_in, response.tokens_out) == (12, 7)
    assert response.provider == "groq-llama"
    assert response.model == "model-x"
    assert response.cache_hit is False
    assert str(seen[0].url) == "https://llm.example.test/v1/chat/completions"
    assert seen[0].headers["Authorization"] == "Bearer sk-test-key"
    body = json.loads(seen[0].content)
    assert body["model"] == "model-x"
    assert body["temperature"] == 0.0


async def test_native_tool_call_arguments_parsed() -> None:
    response = await _call(
        lambda _: httpx.Response(200, json=GROQ_TOOL), request=_request(tools=[SEARCH])
    )

    assert response.content is None
    assert response.tool_calls == [
        ToolCall(id="call_abc", name="search_documents", arguments={"query": "net kar"})
    ]


async def test_tool_call_dict_arguments_accepted() -> None:
    response = await _call(lambda _: httpx.Response(200, json=GITHUB_TOOL_DICT_ARGS))
    assert response.tool_calls[0].arguments == {"query": "x"}


async def test_missing_usage_is_zero() -> None:
    response = await _call(lambda _: httpx.Response(200, json=OPENROUTER_NO_USAGE))
    assert (response.tokens_in, response.tokens_out) == (0, 0)


async def test_json_mode_tool_calls_for_non_native_provider() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=CLOUDFLARE_JSON_MODE)

    response = await _call(handler, _provider(native_tools=False), _request(tools=[SEARCH]))

    assert response.tool_calls[0].name == "search_documents"
    assert response.tool_calls[0].arguments == {"query": "y"}
    assert "tools" not in seen[0]
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert seen[0]["messages"][0]["role"] == "system"


async def test_json_mode_content_for_non_native_provider() -> None:
    body = _completion({"content": json.dumps({"tool_calls": [], "content": "done"})})
    response = await _call(
        lambda _: httpx.Response(200, json=body),
        _provider(native_tools=False),
        _request(tools=[SEARCH]),
    )
    assert response.content == "done"


@pytest.mark.parametrize(
    "content",
    ["not json", "[1, 2]", json.dumps({"tool_calls": [{"arguments": {}}], "content": None})],
)
async def test_json_mode_bad_output_is_invalid(content: str) -> None:
    body = _completion({"content": content})
    with pytest.raises(InvalidResponse):
        await _call(
            lambda _: httpx.Response(200, json=body),
            _provider(native_tools=False),
            _request(tools=[SEARCH]),
        )


@pytest.mark.parametrize(
    "body",
    [
        _completion({"role": "assistant", "content": ""}),
        _completion({"role": "assistant", "content": None, "tool_calls": []}),
        _completion(
            {"tool_calls": [{"id": "1", "function": {"name": "f", "arguments": "{not json"}}]}
        ),
        _completion({"tool_calls": [{"id": "1", "function": {"name": "f", "arguments": "[1]"}}]}),
        _completion({"tool_calls": [{"id": "1", "function": {"name": "f", "arguments": 3}}]}),
        _completion({"tool_calls": [{"id": "1", "function": {"arguments": "{}"}}]}),
        {"choices": []},
        {"error": "x"},
        [],
    ],
)
async def test_invalid_responses(body: object) -> None:
    with pytest.raises(InvalidResponse):
        await _call(lambda _: httpx.Response(200, json=body))


async def test_non_json_body_is_invalid() -> None:
    with pytest.raises(InvalidResponse):
        await _call(lambda _: httpx.Response(200, text="<html>"))


async def test_429_with_retry_after() -> None:
    with pytest.raises(RateLimited) as excinfo:
        await _call(lambda _: httpx.Response(429, headers={"Retry-After": "12"}))
    assert excinfo.value.retry_after == 12.0


@pytest.mark.parametrize("header", [None, "Wed, 21 Oct 2026 07:28:00 GMT"])
async def test_429_without_numeric_retry_after(header: str | None) -> None:
    headers = {"Retry-After": header} if header else {}
    with pytest.raises(RateLimited) as excinfo:
        await _call(lambda _: httpx.Response(429, headers=headers))
    assert excinfo.value.retry_after is None


@pytest.mark.parametrize("status", [500, 502, 503, 404])
async def test_server_errors_are_unavailable(status: int) -> None:
    with pytest.raises(Unavailable):
        await _call(lambda _: httpx.Response(status))


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_client_errors_are_config_errors(status: int) -> None:
    with pytest.raises(ProviderConfigRejected) as excinfo:
        await _call(lambda _: httpx.Response(status))
    assert excinfo.value.status == status


@pytest.mark.parametrize("error", [httpx.ReadTimeout("slow"), httpx.ConnectError("refused")])
async def test_transport_errors_are_unavailable(error: Exception) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(Unavailable):
        await _call(handler)


def test_payload_serialises_tool_history_natively() -> None:
    call = ToolCall(id="c1", name="search_documents", arguments={"query": "q"})
    request = ChatRequest(
        messages=[
            ChatMessage(role="user", content="q"),
            ChatMessage(role="assistant", tool_calls=[call]),
            ChatMessage(role="tool", content="result", tool_call_id="c1"),
        ],
        tools=[SEARCH],
    )
    payload = build_payload(_provider(), request)

    assistant = payload["messages"][1]
    assert assistant["tool_calls"][0]["function"]["arguments"] == '{"query": "q"}'
    assert payload["messages"][2] == {"role": "tool", "content": "result", "tool_call_id": "c1"}
    assert payload["tools"][0]["function"]["name"] == "search_documents"


def test_payload_serialises_tool_history_for_json_mode() -> None:
    call = ToolCall(id="c1", name="search_documents", arguments={"query": "q"})
    request = ChatRequest(
        messages=[
            ChatMessage(role="user", content="q"),
            ChatMessage(role="assistant", tool_calls=[call]),
            ChatMessage(role="tool", content="result", tool_call_id="c1"),
        ],
        tools=[SEARCH],
    )
    payload = build_payload(_provider(native_tools=False), request)

    roles = [message["role"] for message in payload["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert (
        json.loads(payload["messages"][2]["content"])["tool_calls"][0]["name"] == "search_documents"
    )


def test_payload_response_schema_mode() -> None:
    schema = {"type": "object", "properties": {"summary": {"type": "string"}}}
    payload = build_payload(_provider(), _request(tools=[SEARCH], response_schema=schema))

    assert payload["response_format"]["json_schema"]["schema"] == schema
    assert "tools" not in payload


def test_gemini_thought_signature_round_trips() -> None:
    """Gemini 3 rejects a follow-up turn unless the call's `extra_content` is sent back."""
    from llm.client import _native_tool_calls

    extra = {"google": {"thought_signature": "c2ln"}}
    calls = _native_tool_calls(
        "gemini",
        {
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search_documents", "arguments": '{"query": "x"}'},
                    "extra_content": extra,
                }
            ]
        },
    )
    assert calls[0].extra == extra
    request = ChatRequest(
        messages=[
            ChatMessage(role="user", content="q"),
            ChatMessage(role="assistant", tool_calls=calls),
            ChatMessage(role="tool", content="r", tool_call_id="call_1"),
        ]
    )
    sent = build_payload(_provider(name="gemini-flash"), request)["messages"][1]["tool_calls"][0]
    assert sent["extra_content"] == extra


def test_extra_content_is_not_part_of_the_cache_key() -> None:
    from llm.cache_key import cache_key
    from llm.types import ChatMessage, ChatRequest, ToolCall

    def request(extra: dict[str, object] | None) -> ChatRequest:
        call = ToolCall(id="1", name="t", arguments={}, extra=extra)
        return ChatRequest(messages=[ChatMessage(role="assistant", tool_calls=[call])])

    assert cache_key(request(None), "m") == cache_key(request({"google": {"a": 1}}), "m")
