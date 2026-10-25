"""planner: one `strong` call for a structured research plan (research §R4).

The plan is validated in code (schema, step count, advice in any goal) with one re-ask; its
estimate is computed in code. After a failed verification (`verify_rounds > 0`) the planner
revises the approved plan; a revision that leaves the approved tickers, periods or step kinds
is dropped and the approved plan is kept, because revisions get no new approval.
"""

import json
import re
from typing import Any

from pydantic import ValidationError

from agent.answer import Language
from agent.graph.context import NodeContext
from agent.graph.state import MemorySnapshot, ResearchState, VerificationResult
from agent.plan import Plan, estimate, plan_json_schema, renumber, validate_edit, within_scope
from agent.prompts import node_prompt, with_schema
from llm.types import ChatMessage, ChatRequest
from tools.registry import escape_delimiters

MAX_TOKENS = 1500
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_REASK = {
    "tr": "Plan doğrulamadan geçmedi. Şu hataları düzelt ve yalnızca JSON'u yeniden ver:\n",
    "en": "The plan failed validation. Fix these errors and return only the JSON:\n",
}
_HINTS = {
    "tr": "Yönlendiricinin bulduğu şirketler: {tickers}; dönemler: {periods}; "
    "metrikler: {metrics}.",
    "en": "Companies the router found: {tickers}; periods: {periods}; metrics: {metrics}.",
}


_LEFT_OUT = {
    "tr": "Takip listesindeki şu şirketler plan sınırını aştığı için kapsam dışı: {tickers}. "
    "Plana ekleme.",
    "en": "These watchlist companies exceed the plan limit and are left out: {tickers}. "
    "Do not add them to the plan.",
}


def _memory_block(memory: MemorySnapshot | None) -> str:
    if memory is None or not (memory.watchlist or memory.default_period_range):
        return ""
    data = escape_delimiters(json.dumps(memory.model_dump(mode="json"), ensure_ascii=False))
    data = data.replace("</user_memory", "&lt;/user_memory")
    return f"\n\n<user_memory>{data}</user_memory>"


def _feedback_block(plan: Plan, verification: VerificationResult | None) -> str:
    failures = [] if verification is None else [f.model_dump() for f in verification.failures]
    data = json.dumps(
        {"approved_plan": plan.model_dump(include={"steps"}), "failures": failures},
        ensure_ascii=False,
    )
    data = escape_delimiters(data).replace("</verifier_feedback", "&lt;/verifier_feedback")
    return f"\n\n<verifier_feedback>{data}</verifier_feedback>"


def _user_message(state: ResearchState, language: Language) -> str:
    text = state["question"]
    router = state.get("router")
    if router is not None and (router.tickers or router.periods or router.metrics):
        text += "\n\n" + _HINTS[language].format(
            tickers=", ".join(router.tickers) or "-",
            periods=", ".join(router.periods) or "-",
            metrics=", ".join(router.metrics) or "-",
        )
    text += _memory_block(state.get("memory"))
    if left_out := state.get("watchlist_left_out"):
        text += "\n\n" + _LEFT_OUT[language].format(tickers=", ".join(left_out))
    approved = state.get("plan")
    if state.get("verify_rounds", 0) > 0 and approved is not None:
        text += _feedback_block(approved, state.get("verification"))
    return text


def parse_plan(content: str | None) -> tuple[Plan | None, list[str]]:
    if not content:
        return None, ["empty response; return the plan JSON"]
    try:
        return Plan.model_validate_json(_FENCE.sub("", content.strip())), []
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(p) for p in e['loc']) or 'plan'}: {e['msg']}"
            for e in exc.errors(include_input=False)[:10]
        ]


async def planner_node(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
    language: Language = state.get("language") or "tr"
    deps = ctx.deps
    schema = plan_json_schema()
    system = with_schema(node_prompt("planner", language), schema).replace(
        "{max_steps}", str(deps.plan_max_steps)
    )
    messages = [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=_user_message(state, language)),
    ]
    approved = state.get("plan") if state.get("plan_approved") else None
    plan: Plan | None = None
    for attempt in range(2):
        if attempt and ctx.usage.calls_left(ctx.budget) < 1:
            break
        response = await ctx.call(
            ChatRequest(messages=messages, response_schema=schema, max_tokens=MAX_TOKENS),
            "strong",
        )
        candidate, errors = parse_plan(response.content)
        if candidate is not None:
            candidate = renumber(candidate)
            reasons = validate_edit(
                candidate, ctx.budget, ctx.usage.model_calls, max_steps=deps.plan_max_steps
            )
            errors = [f"{r.step or 'plan'} {r.field}: {r.reason}" for r in reasons]
            if not errors:
                plan = candidate
                break
        messages = [
            *messages,
            ChatMessage(role="assistant", content=response.content or ""),
            ChatMessage(role="user", content=_REASK[language] + "\n".join(errors)),
        ]

    if approved is not None:
        # A revision: keep the approved plan unless the new one stays within its scope.
        if plan is None or not within_scope(plan, approved):
            return {}
        plan = plan.model_copy(
            update={"revision": approved.revision + 1, "edited": approved.edited}
        )
    elif plan is None:
        return {"plan": None}

    plan = plan.model_copy(update={"estimate": estimate(plan, ctx.budget)})
    await ctx.emit(
        "plan_proposed", {"plan": plan.model_dump(mode="json"), "revision": plan.revision}
    )
    return {"plan": plan}
