"""Run lifecycle: `runs` row, background agent task, Redis event stream, persistence.

Events go to the stream `run:{id}:events` (contracts/sse-events.md) and expire 24 h after the
run finishes. Runs are hosted in the API process (research §R6). A single-engine run left
`running` by a restart is marked `failed` with `error_code = "interrupted"` on startup; a graph
run executes under a Redis lease and is resumed by the supervisor (`agent/supervisor.py`,
research §R7).

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
from langgraph.types import Command
from sqlalchemy import Engine, insert, select, update

from agent.advice import detect_language
from agent.budget import Usage, utcnow
from agent.graph.nodes.decision import Decision
from agent.graph.state import Calculation, ResearchState
from agent.loop import Budget, RunOutcome, Tools, run_agent
from agent.report import Report, finalize_report
from agent.supervisor import Lease, Leases
from agent.telemetry import record_resume, run_span
from api import schema
from api.errors import ApiError, ErrorCode, localized_message
from llm.router import Router
from llm.types import ProvidersUnavailable
from tools.types import SandboxUnavailable, SeenItem

if TYPE_CHECKING:
    from agent.graph.build import Graph

AgentEngine = Literal["graph", "single"]

logger = logging.getLogger(__name__)

STREAM_TTL_SECONDS = 24 * 3600
TERMINAL_EVENTS = frozenset({"final", "error", "run_cancelled", "run_expired"})
# Ends a stream only while it is the newest event: the run is paused for a decision.
PAUSE_EVENTS = frozenset({"awaiting_decision"})
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
class PendingRun:
    """A run row as the approve route checks it before deciding."""

    user_id: uuid.UUID
    status: str
    language: str
    plan: dict[str, Any] | None
    plan_expires_at: datetime | None
    decided_at: datetime | None
    created_at: datetime


DecisionStatus = Literal["running", "cancelled"]


@dataclass(frozen=True)
class InterruptedRun:
    """A `running` graph run row, as the supervisor resumes it."""

    id: uuid.UUID
    user_id: uuid.UUID
    language: str
    decision: str | None
    plan: dict[str, Any] | None
    decided_at: datetime | None


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
        plan_ttl_hours: int = 24,
        now: Callable[[], datetime] = utcnow,
        leases: Leases | None = None,
    ) -> None:
        self._engine = engine
        self._redis = redis
        self._router = router
        self._tools = tools
        self._budget = budget
        self._clock = clock
        self._now = now
        self._tasks: set[asyncio.Task[None]] = set()
        self._graph_tasks: set[asyncio.Task[None]] = set()
        self.leases = leases or Leases(None)
        self.agent_engine: AgentEngine = agent_engine
        self._graph: Graph | None = None
        self._slots = asyncio.Semaphore(max_concurrent_runs)
        # A paused run's stream must outlive the plan's wait plus the usual 24 h.
        self._pause_ttl_seconds = plan_ttl_hours * 3600 + STREAM_TTL_SECONDS
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
    def redis(self) -> StreamRedis:
        return self._redis

    @property
    def graph(self) -> "Graph | None":
        return self._graph

    def attach_graph(self, graph: "Graph | None") -> None:
        """The compiled graph; built after the service because its nodes emit through it."""
        self._graph = graph

    async def graph_values(self, run_id: uuid.UUID) -> dict[str, Any]:
        """The run's latest checkpointed graph state (empty without a graph or checkpoint)."""
        if self._graph is None:
            return {}
        snapshot = await self._graph.aget_state(graph_config(run_id))
        return dict(snapshot.values or {})

    async def pending(self, run_id: uuid.UUID, user_id: uuid.UUID) -> PendingRun | None:
        return await asyncio.to_thread(self._pending, run_id, user_id)

    def _pending(self, run_id: uuid.UUID, user_id: uuid.UUID) -> PendingRun | None:
        runs = schema.runs
        with self._engine.connect() as conn:
            row = conn.execute(
                select(
                    runs.c.user_id,
                    runs.c.status,
                    runs.c.language,
                    runs.c.plan,
                    runs.c.plan_expires_at,
                    runs.c.decided_at,
                    runs.c.created_at,
                ).where(runs.c.id == run_id, runs.c.user_id == user_id)
            ).first()
        return None if row is None else PendingRun(**row._mapping)

    async def decide(
        self,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        action: str,
        decided_at: datetime,
        *,
        plan: dict[str, Any] | None = None,
        before_commit: Callable[[datetime], Awaitable[Any]] | None = None,
    ) -> DecisionStatus:
        """Accept the first decision on a waiting run, in one conditional update (§R5).

        `before_commit` (the deep quota swap) runs inside the transaction: if it raises, the
        update is rolled back and the run keeps waiting. A lost race raises
        `already_decided`; a past expiry `plan_expired`.
        """
        status: DecisionStatus = "cancelled" if action == "cancel" else "running"
        conn = await asyncio.to_thread(self._engine.connect)
        try:
            transaction = await asyncio.to_thread(conn.begin)
            row = await asyncio.to_thread(
                self._claim_decision, conn, run_id, user_id, action, decided_at, status, plan
            )
            if row is None:
                await asyncio.to_thread(transaction.rollback)
                raise ApiError(await asyncio.to_thread(self._decision_error, run_id, decided_at))
            if before_commit is not None:
                try:
                    await before_commit(row)
                except BaseException:
                    await asyncio.to_thread(transaction.rollback)
                    raise
            await asyncio.to_thread(transaction.commit)
        finally:
            await asyncio.to_thread(conn.close)
        return status

    @staticmethod
    def _claim_decision(
        conn: Any,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        action: str,
        decided_at: datetime,
        status: DecisionStatus,
        plan: dict[str, Any] | None,
    ) -> datetime | None:
        runs = schema.runs
        values: dict[str, Any] = {"status": status, "decided_at": decided_at, "decision": action}
        if plan is not None:
            values["plan"] = plan
        if status == "cancelled":
            values["finished_at"] = decided_at
        created_at = conn.execute(
            update(runs)
            .where(
                runs.c.id == run_id,
                runs.c.user_id == user_id,
                runs.c.status == "awaiting_approval",
                runs.c.plan_expires_at > decided_at,
            )
            .values(**values)
            .returning(runs.c.created_at)
        ).scalar_one_or_none()
        return created_at if isinstance(created_at, datetime) else None

    def _decision_error(self, run_id: uuid.UUID, now: datetime) -> ErrorCode:
        runs = schema.runs
        with self._engine.connect() as conn:
            row = conn.execute(
                select(runs.c.status, runs.c.decided_at, runs.c.plan_expires_at).where(
                    runs.c.id == run_id
                )
            ).first()
        if row is None:
            return "not_found"
        if row.decided_at is not None:
            return "already_decided"
        if row.status == "expired" or (
            row.status == "awaiting_approval"
            and row.plan_expires_at is not None
            and row.plan_expires_at <= now
        ):
            return "plan_expired"
        return "not_awaiting_decision"

    async def claim(self, run_id: uuid.UUID) -> Lease | None:
        """The run's lease for this process, or None while another process holds it."""
        return await self.leases.claim(run_id)

    def resume(
        self,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        language: str,
        decision: dict[str, Any],
        lease: Lease | None = None,
    ) -> None:
        """Continue a paused graph run with an accepted decision, in the background.

        The approve route claims `lease` before it records the decision, so the supervisor
        never sees the run `running` without a lease.
        """
        lease = lease or self.leases.lease(run_id)
        self._spawn_graph(self._drive(run_id, user_id, language, Command(resume=decision), lease))

    async def resume_interrupted(
        self, run: InterruptedRun, lease: Lease, resume_count: int
    ) -> None:
        """Continue a run a restart interrupted, from its last checkpoint (supervisor)."""
        graph = self._graph
        assert graph is not None  # noqa: S101  (the supervisor runs only with a graph)
        snapshot = await graph.aget_state(graph_config(run.id))
        graph_input: Any = None
        if snapshot.interrupts and run.decision is not None and run.decided_at is not None:
            # Decided, but the process stopped before it resumed the graph with the decision.
            decision = Decision.model_validate(
                {"action": run.decision, "plan": run.plan, "decided_at": run.decided_at}
            )
            graph_input = Command(resume=decision.model_dump(mode="json"))
        from_node = snapshot.next[0] if snapshot.next else None
        record_resume(run_id=str(run.id), from_node=from_node, resume_count=resume_count)
        await self._emit_service(
            run.id, "run_resumed", {"from_node": from_node, "resume_count": resume_count}
        )
        self._spawn_graph(
            self._drive(run.id, run.user_id, run.language, graph_input, lease, resume_count)
        )

    async def fail_interrupted(self, run: InterruptedRun, lease: Lease) -> None:
        """A run interrupted more than the limit allows ends `failed`/`interrupted_limit`."""
        try:
            await asyncio.to_thread(self._fail, run.id, "interrupted_limit")
            message = localized_message("interrupted_limit", "en" if run.language == "en" else "tr")
            await self._emit_service(
                run.id, "error", {"code": "interrupted_limit", "message": message}
            )
            await self._redis.expire(stream_key(run.id), STREAM_TTL_SECONDS)
        finally:
            await lease.release()

    async def running_graph_runs(self) -> list[InterruptedRun]:
        return await asyncio.to_thread(self._running_graph_runs)

    def _running_graph_runs(self) -> list[InterruptedRun]:
        runs = schema.runs
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(
                    runs.c.id,
                    runs.c.user_id,
                    runs.c.language,
                    runs.c.decision,
                    runs.c.plan,
                    runs.c.decided_at,
                )
                .where(runs.c.status == "running", runs.c.engine == "graph")
                .order_by(runs.c.created_at)
            ).all()
        return [InterruptedRun(**row._mapping) for row in rows]

    async def bump_resume_count(self, run_id: uuid.UUID) -> int:
        return await asyncio.to_thread(self._bump_resume_count, run_id)

    def _bump_resume_count(self, run_id: uuid.UUID) -> int:
        runs = schema.runs
        with self._engine.begin() as conn:
            count = conn.execute(
                update(runs)
                .where(runs.c.id == run_id)
                .values(resume_count=runs.c.resume_count + 1)
                .returning(runs.c.resume_count)
            ).scalar_one()
        return int(count)

    async def expire_plans(self, now: datetime) -> list[uuid.UUID]:
        """Plans past `plan_expires_at` end `expired`, with `run_expired` on their stream."""
        expired = await asyncio.to_thread(self._expire_plans, now)
        for run_id in expired:
            await self._emit_service(run_id, "run_expired", {})
            await self._redis.expire(stream_key(run_id), STREAM_TTL_SECONDS)
        return expired

    def _expire_plans(self, now: datetime) -> list[uuid.UUID]:
        runs = schema.runs
        with self._engine.begin() as conn:
            rows = conn.execute(
                update(runs)
                .where(runs.c.status == "awaiting_approval", runs.c.plan_expires_at <= now)
                .values(status="expired", finished_at=now)
                .returning(runs.c.id)
            ).all()
        return [row.id for row in rows]

    async def finished_graph_runs(self, before: datetime) -> list[uuid.UUID]:
        return await asyncio.to_thread(self._finished_graph_runs, before)

    def _finished_graph_runs(self, before: datetime) -> list[uuid.UUID]:
        runs = schema.runs
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(runs.c.id).where(runs.c.engine == "graph", runs.c.finished_at < before)
            ).all()
        return [row.id for row in rows]

    async def delete_checkpoint(self, run_id: uuid.UUID) -> None:
        graph = self._graph
        if graph is not None and graph.checkpointer is not None:
            await graph.checkpointer.adelete_thread(str(run_id))  # type: ignore[union-attr]

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
            lease = self.leases.lease(run_id)
            await lease.acquire()  # a new run id: nobody else can hold it
            self._spawn_graph(
                self._execute_graph(
                    run_id, user_id, question, language, self._now(), lease, on_early_failure
                )
            )
        else:
            self._spawn(self._execute(run_id, user_id, question, language, on_early_failure))
        return run_id

    def _spawn(self, coroutine: Awaitable[None]) -> None:
        task: asyncio.Task[None] = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _spawn_graph(self, coroutine: Awaitable[None]) -> None:
        task: asyncio.Task[None] = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        self._graph_tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(self._graph_tasks.discard)

    async def cancel_graph_runs(self) -> None:
        """Shutdown: stop graph runs; each releases its lease so the next process resumes it."""
        tasks = list(self._graph_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

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

    async def mark_awaiting(
        self, run_id: str, plan: dict[str, Any] | None, expires_at: datetime
    ) -> bool:
        """The decision node's row update; False when the run was already decided."""
        return await asyncio.to_thread(self._mark_awaiting, uuid.UUID(run_id), plan, expires_at)

    def _mark_awaiting(
        self, run_id: uuid.UUID, plan: dict[str, Any] | None, expires_at: datetime
    ) -> bool:
        runs = schema.runs
        with self._engine.begin() as conn:
            row = conn.execute(
                update(runs)
                .where(runs.c.id == run_id, runs.c.status == "running", runs.c.decided_at.is_(None))
                .values(
                    status="awaiting_approval", plan=plan, plan_expires_at=expires_at, mode="deep"
                )
                .returning(runs.c.id)
            ).first()
        return row is not None

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
        lease: Lease,
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
        await self._drive(run_id, user_id, language, initial, lease, 0, on_early_failure)

    async def _drive(
        self,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        language: str,
        graph_input: Any,
        lease: Lease,
        resume_count: int = 0,
        on_early_failure: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Run (or resume) the graph until it ends or pauses, then persist the outcome.

        The lease is renewed alongside; once it is lost the run stops at the next node
        boundary and leaves the row to the process that holds it now.
        """
        graph = self._graph
        assert graph is not None  # noqa: S101  (graph runs exist only with a graph)
        started = self._clock()
        config = graph_config(run_id)
        paused = False
        keeper = asyncio.ensure_future(lease.keep())
        with run_span(run_id=str(run_id), user_id=str(user_id), language=language) as span:
            try:
                async with self._slots:
                    async for _ in graph.astream(graph_input, config, durability="sync"):
                        if lease.lost:
                            break
                if lease.lost:
                    span.set_attribute("disclosly.status", "lease_lost")
                    logger.warning("run lease lost", extra={"run_id": str(run_id)})
                    return
                snapshot = await graph.aget_state(config)
                if snapshot.next:
                    # Paused at a decision: the decision node marked the row and emitted
                    # `awaiting_decision`; the stream outlives the plan's wait.
                    span.set_attribute("disclosly.status", "awaiting_approval")
                    paused = True
                    return
                values = cast(ResearchState, snapshot.values)
                if values.get("cancelled"):
                    span.set_attribute("disclosly.status", "cancelled")
                    return
                draft = values.get("draft")
                if draft is None:
                    raise RuntimeError("graph ended without a report")
                total_ms = round((self._clock() - started) * 1000, 1)
                report, citations, usage = self._graph_outcome(
                    values, draft, total_ms, resume_count
                )
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
                keeper.cancel()
                self._model_called.discard(str(run_id))
                if not lease.lost:
                    ttl = self._pause_ttl_seconds if paused else STREAM_TTL_SECONDS
                    await self._redis.expire(stream_key(run_id), ttl)
                    await lease.release()

    def _graph_outcome(
        self, values: ResearchState, draft: Report, total_ms: float, resumes: int = 0
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
            "resumes": resumes,
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
                    if event.event in PAUSE_EVENTS and not await self._redis.xread(
                        {key: cursor}, count=1
                    ):
                        # Paused for a decision; the client reconnects with Last-Event-ID.
                        return


def mark_interrupted(engine: Engine) -> int:
    """Startup hook: single-engine runs left `running` by a restart become
    `failed`/`interrupted` (graph runs are resumed by the supervisor instead)."""
    with engine.begin() as conn:
        result = conn.execute(
            update(schema.runs)
            .where(schema.runs.c.status == "running", schema.runs.c.engine == "single")
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
