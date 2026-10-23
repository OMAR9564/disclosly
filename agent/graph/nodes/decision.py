"""decision: pause for the owner's decision on a plan or a deep quota offer (research §R5).

Before pausing, the run row becomes `awaiting_approval` with the plan and its expiry, and the
stream gets `awaiting_decision`; `interrupt()` then ends the run task. The API resumes the
graph with the first accepted `Decision`. LangGraph re-runs this node from the top on resume,
so the row update only applies to a run that has not been decided yet, and the event is sent
only when it applied.
"""

from datetime import datetime, timedelta
from typing import Any, Literal

from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict

from agent.graph.context import NodeContext
from agent.graph.state import ResearchState
from agent.plan import Plan, estimate, renumber

DecisionAction = Literal["approve", "edit", "cancel", "simple"]
DecisionKind = Literal["plan", "deep_quota_offer"]
# Which actions fit which pending decision (contracts/http-api.md).
ALLOWED_ACTIONS: dict[DecisionKind, frozenset[DecisionAction]] = {
    "plan": frozenset({"approve", "edit", "cancel"}),
    "deep_quota_offer": frozenset({"simple", "cancel"}),
}


class Decision(BaseModel):
    """The resume value: an accepted decision, validated by the API before it is sent."""

    model_config = ConfigDict(extra="forbid")

    action: DecisionAction
    plan: Plan | None = None
    decided_at: datetime


async def decision_node(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
    deps = ctx.deps
    kind: DecisionKind = state.get("decision_kind") or "plan"
    plan = state.get("plan") if kind == "plan" else None
    expires_at = deps.clock() + timedelta(hours=deps.plan_ttl_hours)
    plan_json = plan.model_dump(mode="json") if plan is not None else None

    marked = True
    if deps.runs is not None:
        marked = await deps.runs.mark_awaiting(state["run_id"], plan_json, expires_at)
    if marked:
        remaining = 0 if deps.quota is None else await deps.quota.deep_remaining(state["user_id"])
        await ctx.emit(
            "awaiting_decision",
            {
                "kind": kind,
                "plan": plan_json,
                "estimate": plan_json["estimate"] if plan_json else None,
                "deep_remaining": max(remaining, 0),
                "expires_at": expires_at.isoformat(),
            },
        )

    decision = Decision.model_validate(interrupt({"kind": kind}))
    await ctx.emit("decision_made", {"action": decision.action})

    if decision.action == "cancel":
        await ctx.emit("run_cancelled", {})
        return {"cancelled": True, "decision_kind": None}
    if decision.action == "simple":
        simple = deps.budgets["simple"]
        return {
            "mode": "simple",
            "budget": simple,
            "deadline_at": decision.decided_at + timedelta(seconds=simple.deadline_seconds),
            "decision_kind": None,
        }

    deep = deps.budgets["deep"]
    approved = plan
    if decision.action == "edit" and decision.plan is not None:
        approved = renumber(decision.plan).model_copy(update={"edited": True, "revision": 0})
    if approved is None:
        return {"cancelled": True, "decision_kind": None}
    approved = approved.model_copy(update={"estimate": estimate(approved, deep)})
    return {
        "plan": approved,
        "plan_approved": True,
        # The deep clock starts at approval (planning counted toward calls, not time).
        "deadline_at": decision.decided_at + timedelta(seconds=deep.deadline_seconds),
        "decision_kind": None,
    }
