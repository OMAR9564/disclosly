"""Research endpoints (contracts/http-api.md, contracts/sse-events.md).

`POST /v1/research` starts a run; `GET /v1/research/{id}/stream` streams its events as SSE
(resumable with `Last-Event-ID`, `: ping` every 15 s, closed after `final`/`error`, and in
Phase 4 after `awaiting_decision`, `run_cancelled` and `run_expired`);
`GET /v1/research/{id}` returns the stored run; `POST /v1/research/{id}/approve` decides a
waiting research plan (specs/004-multi-agent-graph/contracts/http-api.md). Only the owner can
read or decide a run.
"""

import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from redis.exceptions import TimeoutError as RedisTimeoutError

from agent.budget import ModeBudget, Usage
from agent.graph.nodes.decision import ALLOWED_ACTIONS, Decision, DecisionAction, DecisionKind
from agent.plan import Plan, estimate, parse_edit, renumber, validate_edit
from agent.runs import RunService, StreamEvent
from api.auth.session import CurrentUser
from api.errors import ApiError, message_language
from api.guards import get_guards, ip_rate_limit
from api.guards.quota import Quotas

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/research", tags=["research"], dependencies=[Depends(ip_rate_limit)])

PING_INTERVAL_MS = 15_000
# A run in these states may still add events to its stream.
LIVE_STATUSES = frozenset({"running", "awaiting_approval"})


class ResearchRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class ResearchStarted(BaseModel):
    run_id: uuid.UUID
    quota: Quotas


class DecisionRequest(BaseModel):
    action: DecisionAction
    # Required for `edit`, ignored otherwise; validated in code to return per-step reasons.
    plan: dict[str, Any] | None = None


class DecisionAccepted(BaseModel):
    run_id: uuid.UUID
    status: Literal["running", "cancelled"]
    quota: Quotas


# Model calls a plan has used before approval when the checkpoint does not say (router and
# planner).
PLANNING_CALLS = 2


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
    simple = await quota.take(user.id, language)
    taken_at = datetime.now(UTC)

    async def refund() -> None:
        await quota.refund(user.id, taken_at)

    try:
        run_id = await _service(request).create(user.id, question, on_early_failure=refund)
    except Exception:
        await refund()
        raise
    return ResearchStarted(
        run_id=run_id, quota=Quotas(simple=simple, deep=await quota.deep_status(user.id))
    )


@router.post("/{run_id}/approve", status_code=202)
async def approve_research(
    run_id: uuid.UUID, body: DecisionRequest, user: CurrentUser, request: Request
) -> DecisionAccepted:
    service = _service(request)
    quota = get_guards(request).quota
    language = message_language(request.headers.get("accept-language"))
    pending = await service.pending(run_id, user.id)
    if pending is None:
        raise ApiError("not_found")
    now = datetime.now(UTC)
    if pending.status != "awaiting_approval":
        if pending.decided_at is not None:
            raise ApiError("already_decided")
        raise ApiError("plan_expired" if pending.status == "expired" else "not_awaiting_decision")
    kind: DecisionKind = "plan" if pending.plan is not None else "deep_quota_offer"
    if body.action not in ALLOWED_ACTIONS[kind]:
        raise ApiError("not_awaiting_decision")
    if pending.plan_expires_at is not None and pending.plan_expires_at <= now:
        raise ApiError("plan_expired")

    deep = ModeBudget.deep(request.app.state.settings)
    plan: Plan | None = None
    if body.action == "edit":
        plan = await _edited_plan(service, run_id, body.plan, deep, request)

    async def swap(submitted_at: datetime) -> None:
        await quota.swap_to_deep(user.id, submitted_at, language)

    status = await service.decide(
        run_id,
        user.id,
        body.action,
        now,
        plan=plan.model_dump(mode="json") if plan is not None else None,
        before_commit=swap if body.action in ("approve", "edit") else None,
    )
    decision = Decision(action=body.action, plan=plan, decided_at=now)
    service.resume(run_id, user.id, pending.language, decision.model_dump(mode="json"))
    return DecisionAccepted(run_id=run_id, status=status, quota=await quota.statuses(user.id))


async def _edited_plan(
    service: RunService,
    run_id: uuid.UUID,
    data: dict[str, Any] | None,
    deep: ModeBudget,
    request: Request,
) -> Plan:
    """The edited plan, or `plan_invalid` with per-step reasons (the run keeps waiting)."""
    settings = request.app.state.settings
    parsed = parse_edit(data or {})
    if isinstance(parsed, list):
        reasons = parsed
    else:
        usage = (await service.graph_values(run_id)).get("usage")
        calls_used = usage.model_calls if isinstance(usage, Usage) else PLANNING_CALLS
        reasons = validate_edit(
            parsed,
            deep,
            calls_used,
            max_steps=settings.agent_plan_max_steps,
            rounds=settings.agent_verify_max_rounds,
        )
    if reasons:
        raise ApiError("plan_invalid", details={"reasons": [r.model_dump() for r in reasons]})
    assert isinstance(parsed, Plan)  # noqa: S101  (no reasons means it parsed)
    plan = renumber(parsed).model_copy(update={"edited": True, "revision": 0})
    return plan.model_copy(update={"estimate": estimate(plan, deep)})


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
        if run["status"] not in LIVE_STATUSES and await service.stream_expired(run_id):
            yield _replay_from_row(run)
            return
        try:
            async for event in service.events(run_id, last_event_id, block_ms=PING_INTERVAL_MS):
                if event is None:
                    if await request.is_disconnected():
                        return
                    yield ": ping\n\n"
                    continue
                yield _sse(event)
        except RedisTimeoutError:
            # A closed tab cancels the blocking XREAD, which redis-py reports as a timeout.
            # End the stream; a client that is still there reconnects with Last-Event-ID.
            logger.info("stream read ended", extra={"run_id": str(run_id)})

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _replay_from_row(run: dict[str, Any]) -> str:
    """After the 24 h stream TTL only the stored result is left."""
    if run["status"] in ("cancelled", "expired"):
        event = "run_cancelled" if run["status"] == "cancelled" else "run_expired"
        return _sse(StreamEvent("0-1", event, {"node": None, "attempt": 1}))
    if run["status"] == "failed":
        code = run["error_code"] or "internal_error"
        return _sse(StreamEvent("0-1", "error", {"code": code, "message": code}))
    data = {"status": run["status"], "answer": run["answer"], "citations": run["citations"]}
    return _sse(StreamEvent("0-1", "final", data))
