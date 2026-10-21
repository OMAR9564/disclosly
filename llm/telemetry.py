"""OTel spans for model calls (research §R11).

Only `opentelemetry-api` is installed: without a configured SDK every span is a no-op.
User ids are hashed before they reach a span.
"""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace

from llm.config import Price
from llm.types import ChatResponse, TaskClass

CHAT_SPAN = "gen_ai.chat"

_tracer = trace.get_tracer("llm")


def hash_user_id(user_id: str) -> str:
    """Stable pseudonymous id for spans; the raw user id never leaves the API."""
    return hashlib.sha256(f"disclosly-user:{user_id}".encode()).hexdigest()[:16]


@contextmanager
def chat_span(
    *,
    provider: str,
    model: str,
    task_class: TaskClass,
    user_id: str | None = None,
    tracer: trace.Tracer | None = None,
) -> Iterator[trace.Span]:
    attributes: dict[str, str] = {
        "gen_ai.system": provider,
        "gen_ai.request.model": model,
        "disclosly.task_class": task_class,
    }
    if user_id is not None:
        attributes["disclosly.user_id_hash"] = hash_user_id(user_id)
    span = (tracer or _tracer).start_span(CHAT_SPAN, attributes=attributes)
    with trace.use_span(span, end_on_exit=True):
        yield span


def would_have_cost(response: ChatResponse, price: Price) -> float:
    """USD the call would have cost at list prices; the project itself pays $0."""
    return round(
        (response.tokens_in * price.input + response.tokens_out * price.output) / 1_000_000, 8
    )


def record_response(
    span: trace.Span, response: ChatResponse, price: Price | None = None
) -> None:
    span.set_attribute("gen_ai.usage.input_tokens", response.tokens_in)
    span.set_attribute("gen_ai.usage.output_tokens", response.tokens_out)
    span.set_attribute("disclosly.cache_hit", response.cache_hit)
    span.set_attribute("disclosly.fallback_from", list(response.fallback_from))
    span.set_attribute("disclosly.latency_ms", response.latency_ms)
    if price is not None:
        span.set_attribute("disclosly.would_have_cost_usd", would_have_cost(response, price))
