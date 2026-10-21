"""One OpenAI-compatible chat completion call (research §R1).

Errors are raised as `ProviderError` subclasses so the router can classify them
(contracts/model-router.md, failure classification). Providers with `native_tools: false`
get the tools described in a system message and must answer in JSON-schema output mode.
"""

import json
import time
from typing import Any

import httpx

from llm.config import Provider
from llm.types import ChatMessage, ChatRequest, ChatResponse, ToolCall

TIMEOUT_SECONDS = 15.0

# Output shape for providers without native tool calling.
_EMULATED_TOOLS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tool_calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "arguments": {"type": "object"}},
                "required": ["name", "arguments"],
            },
        },
        "content": {"type": ["string", "null"]},
    },
    "required": ["tool_calls", "content"],
}


class ProviderError(Exception):
    """A provider call failed; the router decides the cooldown from the subclass."""

    def __init__(self, provider: str, detail: str) -> None:
        super().__init__(f"{provider}: {detail}")
        self.provider = provider


class RateLimited(ProviderError):
    def __init__(self, provider: str, retry_after: float | None) -> None:
        super().__init__(provider, "rate limited")
        self.retry_after = retry_after


class Unavailable(ProviderError):
    """5xx, timeout or connection error."""


class InvalidResponse(ProviderError):
    """200 with no content and no tool calls, or unparsable tool arguments."""


class ProviderConfigRejected(ProviderError):
    """400/401/403: the request or the credentials are wrong; a config error."""

    def __init__(self, provider: str, status: int) -> None:
        super().__init__(provider, f"HTTP {status}")
        self.status = status


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _message_payload(message: ChatMessage, native_tools: bool) -> dict[str, Any]:
    if not native_tools and message.role == "tool":
        return {
            "role": "user",
            "content": f"Tool result ({message.tool_call_id}):\n{message.content}",
        }
    if not native_tools and message.tool_calls:
        calls = [{"name": call.name, "arguments": call.arguments} for call in message.tool_calls]
        return {"role": "assistant", "content": json.dumps({"tool_calls": calls, "content": None})}
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                **({"extra_content": call.extra} if call.extra else {}),
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    return payload


def _tools_instruction(request: ChatRequest) -> str:
    tools = [tool.model_dump() for tool in request.tools]
    return (
        "You can call these tools. Reply only with JSON: "
        '{"tool_calls": [{"name": ..., "arguments": {...}}], "content": null} to call tools, '
        'or {"tool_calls": [], "content": "<your reply>"} when done.\n'
        f"Tools: {json.dumps(tools, ensure_ascii=False)}"
    )


def build_payload(provider: Provider, request: ChatRequest) -> dict[str, Any]:
    """The JSON body for `POST {base_url}/chat/completions`."""
    native = provider.native_tools
    messages = [_message_payload(message, native) for message in request.messages]
    payload: dict[str, Any] = {
        "model": provider.model,
        "messages": messages,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
    }
    if request.response_schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": request.response_schema},
        }
    elif request.tools and native:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in request.tools
        ]
    elif request.tools:
        payload["messages"] = [
            {"role": "system", "content": _tools_instruction(request)},
            *messages,
        ]
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "tool_step", "schema": _EMULATED_TOOLS_SCHEMA},
        }
    return payload


def _parse_arguments(provider: str, raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise InvalidResponse(provider, "tool arguments are not JSON")
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        raise InvalidResponse(provider, "tool arguments are not JSON") from None
    if not isinstance(parsed, dict):
        raise InvalidResponse(provider, "tool arguments are not an object")
    return parsed


def _native_tool_calls(provider: str, message: dict[str, Any]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for index, raw in enumerate(message.get("tool_calls") or []):
        function = raw.get("function") or {}
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise InvalidResponse(provider, "tool call without a name")
        call_id = raw.get("id") or f"call_{index}"
        calls.append(
            ToolCall(
                id=str(call_id),
                name=name,
                arguments=_parse_arguments(provider, function.get("arguments")),
                extra=extra if isinstance(extra := raw.get("extra_content"), dict) else None,
            )
        )
    return calls


def _emulated_tool_calls(provider: str, content: str) -> tuple[str | None, list[ToolCall]]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        raise InvalidResponse(provider, "JSON output mode returned non-JSON") from None
    if not isinstance(parsed, dict):
        raise InvalidResponse(provider, "JSON output mode returned a non-object")
    calls: list[ToolCall] = []
    for index, raw in enumerate(parsed.get("tool_calls") or []):
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
            raise InvalidResponse(provider, "tool call without a name")
        calls.append(
            ToolCall(
                id=f"call_{index}",
                name=raw["name"],
                arguments=_parse_arguments(provider, raw.get("arguments")),
            )
        )
    text = parsed.get("content")
    return (text if isinstance(text, str) and text else None), calls


def parse_response(
    provider: Provider, request: ChatRequest, body: object, latency_ms: float
) -> ChatResponse:
    name = provider.name
    choices = body.get("choices") if isinstance(body, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    if not isinstance(body, dict) or not isinstance(message, dict):
        raise InvalidResponse(name, "response has no message")

    content = message.get("content")
    content = content if isinstance(content, str) and content.strip() else None
    emulated = bool(request.tools) and not provider.native_tools and request.response_schema is None
    if emulated and content is not None:
        content, tool_calls = _emulated_tool_calls(name, content)
    else:
        tool_calls = _native_tool_calls(name, message)
    if content is None and not tool_calls:
        raise InvalidResponse(name, "empty content and no tool calls")

    usage = body.get("usage") or {}
    return ChatResponse(
        content=content,
        tool_calls=tool_calls,
        provider=name,
        model=provider.model,
        tokens_in=int(usage.get("prompt_tokens") or 0),
        tokens_out=int(usage.get("completion_tokens") or 0),
        latency_ms=latency_ms,
    )


async def chat_completion(
    http: httpx.AsyncClient, provider: Provider, request: ChatRequest
) -> ChatResponse:
    """Call one provider once. Raises a `ProviderError` subclass on any failure."""
    name = provider.name
    started = time.perf_counter()
    try:
        response = await http.post(
            f"{provider.base_url}/chat/completions",
            json=build_payload(provider, request),
            headers={"Authorization": f"Bearer {provider.api_key.get_secret_value()}"},
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException:
        raise Unavailable(name, "timeout") from None
    except httpx.TransportError:
        raise Unavailable(name, "connection error") from None
    latency_ms = round((time.perf_counter() - started) * 1000, 3)

    status = response.status_code
    if status == 429:
        raise RateLimited(name, _retry_after(response))
    if status >= 500:
        raise Unavailable(name, f"HTTP {status}")
    if status in (400, 401, 403):
        raise ProviderConfigRejected(name, status)
    if status != 200:
        raise Unavailable(name, f"HTTP {status}")
    try:
        body = response.json()
    except ValueError:
        raise InvalidResponse(name, "body is not JSON") from None
    return parse_response(provider, request, body, latency_ms)
