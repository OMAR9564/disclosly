"""OTel spans for agent runs and tool calls (research §R11)."""

from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace

from llm.telemetry import hash_user_id

RUN_SPAN = "agent.run"
TOOL_SPAN = "agent.tool"

_tracer = trace.get_tracer("agent")


@contextmanager
def run_span(
    *, run_id: str, user_id: str, language: str, tracer: trace.Tracer | None = None
) -> Iterator[trace.Span]:
    span = (tracer or _tracer).start_span(
        RUN_SPAN,
        attributes={
            "disclosly.run_id": run_id,
            "disclosly.user_id_hash": hash_user_id(user_id),
            "disclosly.language": language,
        },
    )
    with trace.use_span(span, end_on_exit=True):
        yield span


@contextmanager
def tool_span(*, name: str, tracer: trace.Tracer | None = None) -> Iterator[trace.Span]:
    span = (tracer or _tracer).start_span(TOOL_SPAN, attributes={"disclosly.tool": name})
    with trace.use_span(span, end_on_exit=True):
        yield span
