"""OTel spans for agent runs, tool calls and graph nodes (Phase 3 §R11, Phase 4 §R17)."""

from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace

from llm.telemetry import hash_user_id

RUN_SPAN = "agent.run"
TOOL_SPAN = "agent.tool"
NODE_SPAN = "agent.node"
VERIFY_SPAN = "agent.verify"
RESUME_SPAN = "agent.resume"

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


@contextmanager
def node_span(
    *, node: str, attempt: int, run_id: str, user_id: str, tracer: trace.Tracer | None = None
) -> Iterator[trace.Span]:
    span = (tracer or _tracer).start_span(
        NODE_SPAN,
        attributes={
            "disclosly.node": node,
            "disclosly.attempt": attempt,
            "disclosly.run_id": run_id,
            "disclosly.user_id_hash": hash_user_id(user_id),
        },
    )
    with trace.use_span(span, end_on_exit=True):
        yield span


@contextmanager
def verify_span(*, round_number: int, tracer: trace.Tracer | None = None) -> Iterator[trace.Span]:
    """One verification round; the caller adds the outcome and failure counts by kind."""
    span = (tracer or _tracer).start_span(
        VERIFY_SPAN, attributes={"disclosly.verify.round": round_number}
    )
    with trace.use_span(span, end_on_exit=True):
        yield span


def record_resume(
    *, run_id: str, from_node: str | None, resume_count: int, tracer: trace.Tracer | None = None
) -> None:
    """A run picked up again after a restart, from its last checkpoint."""
    span = (tracer or _tracer).start_span(
        RESUME_SPAN,
        attributes={
            "disclosly.run_id": run_id,
            "disclosly.resume.from_node": from_node or "",
            "disclosly.resume.count": resume_count,
        },
    )
    span.end()
