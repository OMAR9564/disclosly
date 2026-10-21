"""Run lifecycle: `runs` row, background agent task, Redis event stream, persistence.

Events go to the stream `run:{id}:events` (contracts/sse-events.md) and expire 24 h after the
run finishes. Runs are hosted in the API process (research §R6); a run left `running` by a
restart is marked `failed` with `error_code = "interrupted"` on startup.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import Engine, insert, select, update

from agent.advice import detect_language
from agent.loop import Budget, RunOutcome, Tools, run_agent
from agent.telemetry import run_span
from api import schema
from api.errors import ErrorCode, localized_message
from llm.router import Router
from llm.types import ProvidersUnavailable

logger = logging.getLogger(__name__)

STREAM_TTL_SECONDS = 24 * 3600
TERMINAL_EVENTS = frozenset({"final", "error"})


class StreamRedis(Protocol):
    async def xadd(self, name: str, fields: dict[str, str]) -> Any: ...

    async def xread(
        self, streams: dict[str, str], count: int | None = None, block: int | None = None
    ) -> Any: ...

    async def expire(self, name: str, time: int) -> Any: ...

    async def exists(self, *names: str) -> Any: ...


def stream_key(run_id: uuid.UUID) -> str:
    return f"run:{run_id}:events"


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
    ) -> None:
        self._engine = engine
        self._redis = redis
        self._router = router
        self._tools = tools
        self._budget = budget
        self._clock = clock
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def engine(self) -> Engine:
        return self._engine

    async def create(self, user_id: uuid.UUID, question: str) -> uuid.UUID:
        language = detect_language(question)
        run_id = await asyncio.to_thread(self._insert, user_id, question, language)
        task = asyncio.create_task(self._execute(run_id, user_id, question, language))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run_id

    async def wait_idle(self) -> None:
        """Wait for every background run (tests and shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _insert(self, user_id: uuid.UUID, question: str, language: str) -> uuid.UUID:
        with self._engine.begin() as conn:
            run_id = conn.execute(
                insert(schema.runs)
                .values(user_id=user_id, question=question, language=language)
                .returning(schema.runs.c.id)
            ).scalar_one()
        assert isinstance(run_id, uuid.UUID)  # noqa: S101
        return run_id

    async def _emit(self, run_id: uuid.UUID, event: str, data: dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False, default=str)
        await self._redis.xadd(stream_key(run_id), {"event": event, "data": payload})

    async def _execute(
        self, run_id: uuid.UUID, user_id: uuid.UUID, question: str, language: str
    ) -> None:
        started = self._clock()
        first_event_ms: float | None = None

        async def emit(event: str, data: dict[str, Any]) -> None:
            nonlocal first_event_ms
            if first_event_ms is None:
                first_event_ms = round((self._clock() - started) * 1000, 1)
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
            finally:
                await self._redis.expire(stream_key(run_id), STREAM_TTL_SECONDS)

    @staticmethod
    def error_code(exc: Exception) -> ErrorCode:
        if isinstance(exc, ProvidersUnavailable):
            return "providers_unavailable"
        return "internal_error"

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
