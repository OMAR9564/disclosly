"""Response cache key (contracts/model-router.md, research §R3).

`sha256(canonical_json({...}))` over normalised messages, model id, temperature, max_tokens,
tool_schema_version and response_schema. Text is NFC-normalised, trimmed and has runs of
whitespace collapsed, so trivially different prompts share a key.
"""

import hashlib
import json
import re
import unicodedata
from typing import Any

from llm.types import ChatMessage, ChatRequest

_WHITESPACE_RE = re.compile(r"\s+")


def normalise_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def _message(message: ChatMessage) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": None if message.content is None else normalise_text(message.content),
        "tool_calls": [
            {"name": call.name, "arguments": call.arguments} for call in message.tool_calls
        ],
        "tool_call_id": message.tool_call_id,
    }


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def cache_key(request: ChatRequest, model: str) -> str:
    """64 hex chars; identical for requests that differ only in whitespace or Unicode form."""
    payload = {
        "messages": [_message(message) for message in request.messages],
        "model": model,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "tool_schema_version": request.tool_schema_version,
        "response_schema": request.response_schema,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
