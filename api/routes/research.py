"""Research endpoints (contracts/http-api.md, contracts/sse-events.md).

`POST /v1/research` starts a run; `GET /v1/research/{id}/stream` streams its events as SSE
(resumable with `Last-Event-ID`, `: ping` every 15 s, closed after `final`/`error`);
`GET /v1/research/{id}` returns the stored run. Only the owner can read a run.
"""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent.runs import RunService, StreamEvent
from api.auth.session import CurrentUser
from api.errors import ApiError, message_language
from api.guards import get_guards, ip_rate_limit
from api.guards.quota import QuotaStatus

router = APIRouter(prefix="/v1/research", tags=["research"], dependencies=[Depends(ip_rate_limit)])

PING_INTERVAL_MS = 15_000


class ResearchRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class ResearchStarted(BaseModel):
    run_id: uuid.UUID
    quota: QuotaStatus


def _service(request: Request) -> RunService:
    service = request.app.state.run_service
    assert isinstance(service, RunService)  # noqa: S101  (set by create_app)
    return service


def _sse(event: StreamEvent) -> str:
    data = json.dumps(event.data, ensure_ascii=False)
    return f"id: {event.id}\nevent: {event.event}\ndata: {data}\n\n"


@router.post("", status_code=202)
async def start_research(
    body: ResearchRequest, user: CurrentUser, request: Request
) -> ResearchStarted:
    question = body.question.strip()
    if len(question) < 3:
        raise ApiError("validation_error")
    quota = get_guards(request).quota
    language = message_language(request.headers.get("accept-language"))
    status = await quota.take(user.id, language)
    taken_at = datetime.now(UTC)

    async def refund() -> None:
        await quota.refund(user.id, taken_at)

    try:
        run_id = await _service(request).create(user.id, question, on_early_failure=refund)
    except Exception:
        await refund()
        raise
    return ResearchStarted(run_id=run_id, quota=status)


@router.get("/{run_id}")
async def get_research(run_id: uuid.UUID, user: CurrentUser, request: Request) -> dict[str, Any]:
    run = await _service(request).get(run_id, user.id)
    if run is None:
        raise ApiError("not_found")
    return run


@router.get("/{run_id}/stream")
async def stream_research(
    run_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    service = _service(request)
    run = await service.get(run_id, user.id)
    if run is None:
        raise ApiError("not_found")

    async def body() -> AsyncIterator[str]:
        if run["status"] != "running" and await service.stream_expired(run_id):
            yield _replay_from_row(run)
            return
        async for event in service.events(run_id, last_event_id, block_ms=PING_INTERVAL_MS):
            if event is None:
                if await request.is_disconnected():
                    return
                yield ": ping\n\n"
                continue
            yield _sse(event)

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _replay_from_row(run: dict[str, Any]) -> str:
    """After the 24 h stream TTL only the stored result is left."""
    if run["status"] == "failed":
        code = run["error_code"] or "internal_error"
        return _sse(StreamEvent("0-1", "error", {"code": code, "message": code}))
    data = {"status": run["status"], "answer": run["answer"], "citations": run["citations"]}
    return _sse(StreamEvent("0-1", "final", data))
