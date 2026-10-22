"""Run lifecycle: `runs` row, background agent task, Redis event stream, persistence.

Events go to the stream `run:{id}:events` (contracts/sse-events.md) and expire 24 h after the
run finishes. Runs are hosted in the API process (research §R6); a run left `running` by a
restart is marked `failed` with `error_code = "interrupted"` on startup.

`AGENT_ENGINE` picks the engine for new runs (Phase 4, research §R15): `single` runs the
Phase 3 loop unchanged; `graph` runs the LangGraph research graph, checkpointed with the run
id as its thread id. Graph events carry `node` and `attempt`
(specs/004-multi-agent-graph/contracts/sse-events.md), and at most
`AGENT_MAX_CONCURRENT_RUNS` graph runs execute at once per worker; waiting counts toward the
run's deadline.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from langchain_core.runnables import RunnableConfig
from sqlalchemy import Engine, insert, select, update

from agent.advice import detect_language
from agent.budget import Usage, utcnow
from agent.graph.state import Calculation, ResearchState
from agent.loop import Budget, RunOutcome, Tools, run_agent
from agent.report import Report, finalize_report
from agent.telemetry import run_span
from api import schema
from api.errors import ErrorCode, localized_message
from llm.router import Router
from llm.types import ProvidersUnavailable
from tools.types import SandboxUnavailable, SeenItem

if TYPE_CHECKING:
    from agent.graph.build import Graph

AgentEngine = Literal["graph", "single"]

logger = logging.getLogger(__name__)

STREAM_TTL_SECONDS = 24 * 3600
TERMINAL_EVENTS = frozenset({"final", "error"})
ATTEMPT_TTL_SECONDS = 48 * 3600
EXCERPT_MAX_CHARS = 1500
CODE_MAX_CHARS = 4000


class StreamRedis(Protocol):
    async def xadd(self, name: str, fields: dict[str, str]) -> Any: ...

    async def xread(
        self, streams: dict[str, str], count: int | None = None, block: int | None = None
    ) -> Any: ...

    async def expire(self, name: str, time: int) -> Any: ...

    async def exists(self, *names: str) -> Any: ...

    async def incr(self, name: str) -> Any: ...


def stream_key(run_id: uuid.UUID | str) -> str:
    return f"run:{run_id}:events"


def attempt_key(run_id: str, node: str, step: int) -> str:
    return f"run:{run_id}:attempt:{node}:{step}"


def graph_config(run_id: uuid.UUID | str) -> RunnableConfig:
    return {"configurable": {"thread_id": str(run_id)}}


@dataclass(frozen=True)
class StreamEvent:
    id: str
    event: str
    data: dict[str, Any]


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


class RunService:
    def __init__(
        self,
        engine: Engine,
        redis: StreamRedis,
        router: Router,
        tools: Tools,
        budget: Budget,
        *,
        clock: Callable[[], float] = time.monotonic,
        agent_engine: AgentEngine = "single",
        max_concurrent_runs: int = 3,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._engine = engine
        self._redis = redis
        self._router = router
        self._tools = tools
        self._budget = budget
        self._clock = clock
        self._now = now
        self._tasks: set[asyncio.Task[None]] = set()
        self.agent_engine: AgentEngine = agent_engine
        self._graph: Graph | None = None
        self._slots = asyncio.Semaphore(max_concurrent_runs)
        self._model_called: set[str] = set()

    @property
    def engine(self) -> Engine:
        return self._engine

    @property
    def router(self) -> Router:
        return self._router

    @property
    def tools(self) -> Tools:
        return self._tools

    @property
    def graph(self) -> "Graph | None":
        return self._graph

    def attach_graph(self, graph: "Graph | None") -> None:
        """The compiled graph; built after the service because its nodes emit through it."""
        self._graph = graph

    def _engine_for_new_runs(self) -> AgentEngine:
        return "graph" if self.agent_engine == "graph" and self._graph is not None else "single"

    async def create(
        self,
        user_id: uuid.UUID,
        question: str,
        *,
        on_early_failure: Callable[[], Awaitable[None]] | None = None,
    ) -> uuid.UUID:
        """Start a run. `on_early_failure` runs if it fails before the first model call
        (the quota refund)."""
        language = detect_language(question)
        engine = self._engine_for_new_runs()
        run_id = await asyncio.to_thread(self._insert, user_id, question, language, engine)
        if engine == "graph":
            self._spawn(
                self._execute_graph(
                    run_id, user_id, question, language, self._now(), on_early_failure
                )
            )
        else:
            self._spawn(self._execute(run_id, user_id, question, language, on_early_failure))
        return run_id

    def _spawn(self, coroutine: Awaitable[None]) -> None:
        task: asyncio.Task[None] = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_idle(self) -> None:
        """Wait for every background run (tests and shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _insert(
        self, user_id: uuid.UUID, question: str, language: str, engine: AgentEngine = "single"
    ) -> uuid.UUID:
        with self._engine.begin() as conn:
            run_id = conn.execute(
                insert(schema.runs)
                .values(user_id=user_id, question=question, language=language, engine=engine)
                .returning(schema.runs.c.id)
            ).scalar_one()
        assert isinstance(run_id, uuid.UUID)  # noqa: S101
        return run_id

    async def _emit(self, run_id: uuid.UUID, event: str, data: dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False, default=str)
        await self._redis.xadd(stream_key(run_id), {"event": event, "data": payload})

    async def _execute(
        self,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        question: str,
        language: str,
        on_early_failure: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        started = self._clock()
        first_event_ms: float | None = None
        model_called = False

        async def emit(event: str, data: dict[str, Any]) -> None:
            nonlocal first_event_ms, model_called
            if first_event_ms is None:
                first_event_ms = round((self._clock() - started) * 1000, 1)
            if event == "model_called":
                model_called = True
            await self._emit(run_id, event, data)

        with run_span(run_id=str(run_id), user_id=str(user_id), language=language) as span:
            try:
                await self._emit(
                    run_id, "run_started", {"run_id": str(run_id), "language": language}
                )
                outcome = await run_agent(question, self._router, self._tools, self._budget, emit)
                total_ms = round((self._clock() - started) * 1000, 1)
                usage = {
                    **outcome.usage,
                    "time_to_first_event_ms": first_event_ms,
                    "total_ms": total_ms,
                }
                span.set_attribute("disclosly.time_to_first_event_ms", first_event_ms or 0.0)
                span.set_attribute("disclosly.total_ms", total_ms)
                span.set_attribute("disclosly.status", outcome.status)
                await asyncio.to_thread(self._finish, run_id, outcome, usage)
                await self._emit(
                    run_id,
                    "final",
                    {
                        "status": outcome.status,
                        "answer": outcome.answer.model_dump(mode="json"),
                        "citations": outcome.citations,
                    },
                )
            except Exception as exc:
                logger.exception("run failed", extra={"run_id": str(run_id)})
                span.set_attribute("disclosly.status", "failed")
                code = self.error_code(exc)
                await asyncio.to_thread(self._fail, run_id, code)
                message = localized_message(code, "en" if language == "en" else "tr")
                await self._emit(run_id, "error", {"code": code, "message": message})
                if on_early_failure is not None and not model_called:
                    try:
                        await on_early_failure()
                    except Exception:
                        logger.exception("early-failure hook failed", extra={"run_id": str(run_id)})
            finally:
                await self._redis.expire(stream_key(run_id), STREAM_TTL_SECONDS)

    @staticmethod
    def error_code(exc: Exception) -> ErrorCode:
        if isinstance(exc, ProvidersUnavailable):
            return "providers_unavailable"
        if isinstance(exc, SandboxUnavailable):
            return "sandbox_unavailable"
        return "internal_error"

    # --- graph engine -------------------------------------------------------------------

    async def emit_event(self, run_id: str, event: str, data: dict[str, Any]) -> None:
        """The graph nodes' emitter: `data` already carries `node` and `attempt`."""
        if event == "model_called":
            self._model_called.add(run_id)
        await self._emit(uuid.UUID(run_id), event, data)

    async def attempt(self, run_id: str, node: str, step: int) -> int:
        """1 for a node's first execution at a graph step, +1 for each re-run (retry, resume)."""
        key = attempt_key(run_id, node, step)
        value = int(await self._redis.incr(key))
        await self._redis.expire(key, ATTEMPT_TTL_SECONDS)
        return value

    async def _emit_service(self, run_id: uuid.UUID, event: str, data: dict[str, Any]) -> None:
        """Events the service itself emits for a graph run (no node)."""
        await self._emit(run_id, event, {**data, "node": None, "attempt": 1})

    async def _execute_graph(
        self,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        question: str,
        language: str,
        submitted_at: datetime,
        on_early_failure: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        initial: ResearchState = {
            "run_id": str(run_id),
            "user_id": str(user_id),
            "question": question,
            "language": "en" if language == "en" else "tr",
            "submitted_at": submitted_at,
            "usage": Usage(),
            "evidence": [],
            "calculations": [],
            "errors": [],
            "verify_rounds": 0,
            "plan_approved": False,
            "cancelled": False,
        }
        await self._emit_service(
            run_id, "run_started", {"run_id": str(run_id), "language": language}
        )
        await self._drive(run_id, user_id, language, initial, on_early_failure)

    async def _drive(
        self,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        language: str,
        graph_input: Any,
        on_early_failure: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Run (or resume) the graph until it ends or pauses, then persist the outcome."""
        graph = self._graph
        assert graph is not None  # noqa: S101  (graph runs exist only with a graph)
        started = self._clock()
        config = graph_config(run_id)
        with run_span(run_id=str(run_id), user_id=str(user_id), language=language) as span:
            try:
                async with self._slots:
                    async for _ in graph.astream(graph_input, config, durability="sync"):
                        pass
                snapshot = await graph.aget_state(config)
                if snapshot.next:
                    # Paused at a decision: the decision node marked the row and emitted
                    # `awaiting_decision`; the stream outlives the plan's wait.
                    span.set_attribute("disclosly.status", "awaiting_approval")
                    return
                values = cast(ResearchState, snapshot.values)
                if values.get("cancelled"):
                    span.set_attribute("disclosly.status", "cancelled")
                    return
                draft = values.get("draft")
                if draft is None:
                    raise RuntimeError("graph ended without a report")
                total_ms = round((self._clock() - started) * 1000, 1)
                report, citations, usage = self._graph_outcome(values, draft, total_ms)
                span.set_attribute("disclosly.status", report.status)
                await asyncio.to_thread(
                    self._finish_graph, run_id, report, citations, usage, values.get("mode")
                )
                await self._emit_service(
                    run_id,
                    "final",
                    {
                        "status": report.status,
                        "answer": report.model_dump(mode="json"),
                        "citations": citations,
                    },
                )
            except Exception as exc:
                logger.exception("run failed", extra={"run_id": str(run_id)})
                span.set_attribute("disclosly.status", "failed")
                code = self.error_code(exc)
                await asyncio.to_thread(self._fail, run_id, code)
                message = localized_message(code, "en" if language == "en" else "tr")
                await self._emit_service(run_id, "error", {"code": code, "message": message})
                if on_early_failure is not None and str(run_id) not in self._model_called:
                    try:
                        await on_early_failure()
                    except Exception:
                        logger.exception("early-failure hook failed", extra={"run_id": str(run_id)})
            finally:
                self._model_called.discard(str(run_id))
                await self._redis.expire(stream_key(run_id), STREAM_TTL_SECONDS)

    def _graph_outcome(
        self, values: ResearchState, draft: Report, total_ms: float
    ) -> tuple[Report, list[dict[str, Any]], dict[str, Any]]:
        report = finalize_report(draft)
        evidence = {item.cite_id: item for item in values.get("evidence") or []}
        calculations = {c.cite_id: c for c in values.get("calculations") or []}
        citations = graph_citations(report, evidence, calculations)
        usage_model = values.get("usage") or Usage()
        usage = {
            **usage_model.model_dump(mode="json"),
            "limit_hit": values.get("limit_hit"),
            "verify_rounds": values.get("verify_rounds", 0),
            "total_ms": total_ms,
        }
        return report, citations, usage

    def _finish_graph(
        self,
        run_id: uuid.UUID,
        report: Report,
        citations: list[dict[str, Any]],
        usage: dict[str, Any],
        mode: str | None,
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(schema.runs)
                .where(schema.runs.c.id == run_id)
                .values(
                    status=report.status,
                    answer=report.model_dump(mode="json"),
                    usage=usage,
                    mode=mode,
                    finished_at=datetime.now(UTC),
                )
            )
            if citations:
                conn.execute(
                    insert(schema.run_citations),
                    [{"run_id": run_id, **citation} for citation in citations],
                )

    def _finish(self, run_id: uuid.UUID, outcome: RunOutcome, usage: dict[str, Any]) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(schema.runs)
                .where(schema.runs.c.id == run_id)
                .values(
                    status=outcome.status,
                    answer=outcome.answer.model_dump(mode="json"),
                    usage=usage,
                    finished_at=datetime.now(UTC),
                )
            )
            if outcome.citations:
                conn.execute(
                    insert(schema.run_citations),
                    [
                        {
                            "run_id": run_id,
                            "cite_id": c["cite_id"],
                            "excerpt": c["excerpt"],
                            "source": c["source"],
                        }
                        for c in outcome.citations
                    ],
                )

    def _fail(self, run_id: uuid.UUID, code: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(schema.runs)
                .where(schema.runs.c.id == run_id)
                .values(status="failed", error_code=code, finished_at=datetime.now(UTC))
            )

    async def get(self, run_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, Any] | None:
        """The run with its citations, or None when missing or owned by someone else."""
        return await asyncio.to_thread(self._get, run_id, user_id)

    def _get(self, run_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, Any] | None:
        runs, cites = schema.runs, schema.run_citations
        with self._engine.connect() as conn:
            row = conn.execute(
                select(runs).where(runs.c.id == run_id, runs.c.user_id == user_id)
            ).first()
            if row is None:
                return None
            citations = [
                {"cite_id": c.cite_id, "excerpt": c.excerpt, "source": c.source}
                for c in conn.execute(
                    select(cites).where(cites.c.run_id == run_id).order_by(cites.c.cite_id)
                )
            ]
        return {
            "run_id": str(row.id),
            "status": row.status,
            "question": row.question,
            "language": row.language,
            "answer": row.answer,
            "citations": citations,
            "usage": row.usage,
            "error_code": row.error_code,
            "created_at": row.created_at.isoformat(),
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "engine": row.engine,
            "mode": row.mode,
            "plan": row.plan,
            "plan_expires_at": row.plan_expires_at.isoformat() if row.plan_expires_at else None,
            "decision": row.decision,
        }

    async def stream_expired(self, run_id: uuid.UUID) -> bool:
        return not await self._redis.exists(stream_key(run_id))

    async def events(
        self, run_id: uuid.UUID, last_id: str | None = None, *, block_ms: int = 15_000
    ) -> AsyncIterator[StreamEvent | None]:
        """Stream events after `last_id` (all when None); yields None on each idle block.

        Stops after a terminal event.
        """
        cursor = last_id or "0-0"
        key = stream_key(run_id)
        while True:
            response = await self._redis.xread({key: cursor}, count=100, block=block_ms)
            if not response:
                yield None
                continue
            for _, entries in response:
                for entry_id, fields in entries:
                    cursor = _text(entry_id)
                    decoded = {_text(k): _text(v) for k, v in fields.items()}
                    event = StreamEvent(cursor, decoded["event"], json.loads(decoded["data"]))
                    yield event
                    if event.event in TERMINAL_EVENTS:
                        return


def mark_interrupted(engine: Engine) -> int:
    """Startup hook: runs left `running` by a restart become `failed`/`interrupted`."""
    with engine.begin() as conn:
        result = conn.execute(
            update(schema.runs)
            .where(schema.runs.c.status == "running")
            .values(status="failed", error_code="interrupted", finished_at=datetime.now(UTC))
        )
    return result.rowcount


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def calculation_citation(calc: Calculation) -> dict[str, Any]:
    """A `k:` citation: description and formula as the excerpt, inputs and code as source."""
    excerpt = f"{calc.description} ({calc.name})" if calc.description else calc.name
    return {
        "cite_id": calc.cite_id,
        "excerpt": excerpt[:EXCERPT_MAX_CHARS],
        "source": {
            "kind": "calculation",
            "name": calc.name,
            "inputs": [
                {"name": i.name, "cite_id": i.cite_id, "value": _decimal_text(i.value)}
                for i in calc.inputs
            ],
            "code": calc.code[:CODE_MAX_CHARS],
            "output": _decimal_text(calc.output),
        },
    }


def graph_citations(
    report: Report, evidence: dict[str, SeenItem], calculations: dict[str, Calculation]
) -> list[dict[str, Any]]:
    """Citations for every id the report uses, plus each calculation input's own source."""
    ordered = report.cite_ids()
    for cite_id in list(ordered):
        if cite_id in calculations:
            ordered += [i.cite_id for i in calculations[cite_id].inputs]
    found = []
    for cite_id in dict.fromkeys(ordered):
        if cite_id in calculations:
            found.append(calculation_citation(calculations[cite_id]))
        elif cite_id in evidence:
            item = evidence[cite_id]
            found.append(
                {
                    "cite_id": cite_id,
                    "excerpt": item.text[:EXCERPT_MAX_CHARS],
                    "source": item.source,
                }
            )
    return found
