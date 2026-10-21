"""Tests for llm.cache_key (T009)."""

import unicodedata
from typing import Any

import pytest

from llm.cache_key import cache_key, normalise_text
from llm.types import ChatMessage, ChatRequest, ToolCall


def _request(text: str = "THYAO net kâr?", **kwargs: Any) -> ChatRequest:
    return ChatRequest(
        messages=[
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content=text),
        ],
        **kwargs,
    )


def test_key_is_sha256_hex() -> None:
    key = cache_key(_request(), "m")
    assert len(key) == 64
    assert int(key, 16) >= 0


def test_whitespace_and_unicode_form_do_not_change_key() -> None:
    decomposed = unicodedata.normalize("NFD", "THYAO net kâr?")
    assert decomposed != "THYAO net kâr?"
    base = cache_key(_request(), "m")

    assert cache_key(_request("  THYAO   net\n kâr?\t"), "m") == base
    assert cache_key(_request(decomposed), "m") == base


@pytest.mark.parametrize(
    ("kwargs", "model"),
    [
        ({}, "other-model"),
        ({"temperature": 0.5}, "m"),
        ({"max_tokens": 10}, "m"),
        ({"tool_schema_version": "2"}, "m"),
        ({"response_schema": {"type": "object"}}, "m"),
    ],
)
def test_key_fields_change_key(kwargs: dict[str, Any], model: str) -> None:
    assert cache_key(_request(**kwargs), model) != cache_key(_request(), "m")


def test_content_change_changes_key() -> None:
    assert cache_key(_request("THYAO net kar?"), "m") != cache_key(_request(), "m")


def test_tool_history_is_part_of_key() -> None:
    call = ToolCall(id="random-id-1", name="f", arguments={"b": 1, "a": 2})
    same_call_other_id = ToolCall(id="random-id-2", name="f", arguments={"a": 2, "b": 1})
    first = ChatRequest(messages=[ChatMessage(role="assistant", tool_calls=[call])])
    second = ChatRequest(messages=[ChatMessage(role="assistant", tool_calls=[same_call_other_id])])
    other = ChatRequest(
        messages=[ChatMessage(role="assistant", tool_calls=[call.model_copy(update={"name": "g"})])]
    )

    assert cache_key(first, "m") == cache_key(second, "m")
    assert cache_key(first, "m") != cache_key(other, "m")


def test_normalise_text() -> None:
    assert normalise_text("  a " + chr(0xA0) + " b\n\nc ") == "a b c"
