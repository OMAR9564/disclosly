"""quota_gate: route a deep question to the planner, or offer simple mode (research §R6).

Reads the deep quota without taking it; the API takes it when the plan is approved. With no
deep unit left the run goes straight to the decision with a `deep_quota_offer`, so no planner
call is spent on a plan the user could not approve.
"""

from typing import Any

from agent.graph.context import NodeContext
from agent.graph.state import ResearchState


async def quota_gate_node(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
    quota = ctx.deps.quota
    remaining = 1 if quota is None else await quota.deep_remaining(state["user_id"])
    return {"decision_kind": "deep_quota_offer" if remaining <= 0 else None}
