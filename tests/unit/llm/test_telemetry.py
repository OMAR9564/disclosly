"""Tests for llm.telemetry, agent.telemetry and email masking (T011).

The repo ships only `opentelemetry-api`, so a small recording tracer stands in for the SDK's
in-memory exporter.
"""

from collections.abc import Mapping
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.util.types import AttributeValue

from agent.telemetry import RUN_SPAN, TOOL_SPAN, run_span, tool_span
from api.logging import mask_email
from llm.telemetry import CHAT_SPAN, chat_span, hash_user_id, record_response
from llm.types import ChatResponse

USER_ID = "11111111-2222-4333-8444-555555555555"


class RecordingSpan(trace.NonRecordingSpan):
    def __init__(self, name: str, attributes: Mapping[str, AttributeValue] | None) -> None:
        super().__init__(trace.INVALID_SPAN_CONTEXT)
        self.name = name
        self.attributes: dict[str, Any] = dict(attributes or {})
        self.ended = False

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        self.attributes[key] = value

    def end(self, end_time: int | None = None) -> None:
        self.ended = True


class RecordingTracer(trace.NoOpTracer):
    def __init__(self) -> None:
        self.spans: list[RecordingSpan] = []

    def start_span(
        self, name: str, *args: Any, attributes: Any = None, **kwargs: Any
    ) -> trace.Span:
        span = RecordingSpan(name, attributes)
        self.spans.append(span)
        return span


@pytest.fixture
def tracer() -> RecordingTracer:
    return RecordingTracer()


def test_chat_span_attributes(tracer: RecordingTracer) -> None:
    response = ChatResponse(
        content="x",
        provider="groq-llama",
        model="llama",
        tokens_in=11,
        tokens_out=4,
        latency_ms=12.5,
        cache_hit=True,
        fallback_from=["gemini-flash-lite"],
    )
    with chat_span(
        provider="groq-llama", model="llama", task_class="cheap", user_id=USER_ID, tracer=tracer
    ) as span:
        record_response(span, response)

    (recorded,) = tracer.spans
    assert recorded.name == CHAT_SPAN
    assert recorded.ended
    assert recorded.attributes == {
        "gen_ai.system": "groq-llama",
        "gen_ai.request.model": "llama",
        "disclosly.task_class": "cheap",
        "disclosly.user_id_hash": hash_user_id(USER_ID),
        "gen_ai.usage.input_tokens": 11,
        "gen_ai.usage.output_tokens": 4,
        "disclosly.cache_hit": True,
        "disclosly.fallback_from": ["gemini-flash-lite"],
        "disclosly.latency_ms": 12.5,
    }
    assert USER_ID not in str(recorded.attributes)


def test_chat_span_without_user(tracer: RecordingTracer) -> None:
    with chat_span(provider="p", model="m", task_class="strong", tracer=tracer):
        pass
    assert "disclosly.user_id_hash" not in tracer.spans[0].attributes


def test_chat_span_ends_on_error(tracer: RecordingTracer) -> None:
    with (
        pytest.raises(RuntimeError),
        chat_span(provider="p", model="m", task_class="cheap", tracer=tracer),
    ):
        raise RuntimeError("boom")
    assert tracer.spans[0].ended


def test_default_tracer_is_noop() -> None:
    with chat_span(provider="p", model="m", task_class="cheap") as span:
        record_response(span, ChatResponse(content="x", provider="p", model="m"))
    assert not span.is_recording()


def test_hash_user_id_is_stable_and_opaque() -> None:
    assert hash_user_id(USER_ID) == hash_user_id(USER_ID)
    assert hash_user_id(USER_ID) != hash_user_id("other")
    assert len(hash_user_id(USER_ID)) == 16


def test_run_and_tool_spans(tracer: RecordingTracer) -> None:
    with (
        run_span(run_id="r1", user_id=USER_ID, language="tr", tracer=tracer),
        tool_span(name="search_documents", tracer=tracer),
    ):
        pass

    run, tool = tracer.spans
    assert run.name == RUN_SPAN
    assert run.attributes == {
        "disclosly.run_id": "r1",
        "disclosly.user_id_hash": hash_user_id(USER_ID),
        "disclosly.language": "tr",
    }
    assert tool.name == TOOL_SPAN
    assert tool.attributes == {"disclosly.tool": "search_documents"}
    assert run.ended and tool.ended


@pytest.mark.parametrize(
    ("email", "masked"),
    [
        ("ayse@disclosly.com", "a***@d***.com"),
        ("Bob.Smith@mail.example.org", "B***@m***.org"),
        ("x@localhost", "x***@***"),
        ("not-an-email", "***"),
        ("@domain.com", "***"),
        ("a@", "***"),
    ],
)
def test_mask_email(email: str, masked: str) -> None:
    assert mask_email(email) == masked


def test_would_have_cost_is_recorded(tracer: RecordingTracer) -> None:
    from llm.config import Price
    from llm.telemetry import would_have_cost

    price = Price(input=0.1, output=0.4)
    response = ChatResponse(
        content="x", provider="p", model="m", tokens_in=1000, tokens_out=500, cache_hit=True
    )
    assert would_have_cost(response, price) == 0.0003
    with chat_span(provider="p", model="m", task_class="cheap", tracer=tracer) as span:
        record_response(span, response, price)
    attributes = tracer.spans[0].attributes
    assert attributes["disclosly.would_have_cost_usd"] == 0.0003
    assert attributes["disclosly.cache_hit"] is True
