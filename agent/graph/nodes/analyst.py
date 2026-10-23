"""analyst: one call that turns the gathered evidence into cited findings (research §R9).

Evidence reaches the model wrapped as untrusted documents. The reply must match
`AnalystOutput`; an invalid reply is re-asked once while a call is left. Findings citing an id
that is not in the evidence are dropped. The task class is `strong` when the plan has a
`calculate` step (the calculations themselves arrive with T035).
"""

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.answer import Language
from agent.graph.context import NodeContext
from agent.graph.state import Finding, ResearchState
from agent.plan import Plan
from agent.prompts import node_prompt, with_schema
from llm.types import ChatMessage, ChatRequest, TaskClass
from tools.registry import wrap
from tools.types import SeenItem

MAX_FINDINGS = 30
MAX_EVIDENCE_ITEMS = 60
EVIDENCE_ITEM_CHARS = 1500
MAX_TOKENS = 2048
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_REASK = {
    "tr": "Yanıtın şemaya uymadı. Şu hataları düzelt ve yalnızca JSON'u yeniden ver:\n",
    "en": "Your reply did not match the schema. Fix these errors and return only the JSON:\n",
}


class AnalystOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[Finding] = Field(max_length=MAX_FINDINGS)


def task_class(plan: Plan | None) -> TaskClass:
    return "strong" if plan is not None and "calculate" in plan.kinds() else "cheap"


def evidence_block(items: list[SeenItem]) -> str:
    shown = items[:MAX_EVIDENCE_ITEMS]
    return "\n".join(
        wrap(item.model_copy(update={"text": item.text[:EVIDENCE_ITEM_CHARS]})) for item in shown
    )


def _plan_block(plan: Plan | None) -> str:
    if plan is None:
        return ""
    steps = [
        s.model_dump(include={"id", "kind", "goal", "tickers", "periods", "status"})
        for s in plan.steps
    ]
    return "\n\n" + json.dumps({"plan": steps}, ensure_ascii=False)


def parse_output(content: str | None) -> tuple[AnalystOutput | None, list[str]]:
    if not content:
        return None, ["empty response; return the findings JSON"]
    try:
        return AnalystOutput.model_validate_json(_FENCE.sub("", content.strip())), []
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(p) for p in e['loc']) or 'output'}: {e['msg']}"
            for e in exc.errors(include_input=False)[:10]
        ]


async def analyst_node(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
    language: Language = state.get("language") or "tr"
    evidence = state.get("evidence") or []
    known = {item.cite_id for item in evidence}
    schema = AnalystOutput.model_json_schema()
    system = with_schema(node_prompt("analyst", language), schema)
    user = f"{state['question']}{_plan_block(state.get('plan'))}\n\n{evidence_block(evidence)}"
    messages = [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=user),
    ]
    output: AnalystOutput | None = None
    for attempt in range(2):
        if attempt and ctx.usage.calls_left(ctx.budget) < 1:
            break
        response = await ctx.call(
            ChatRequest(messages=messages, response_schema=schema, max_tokens=MAX_TOKENS),
            task_class(state.get("plan")),
        )
        output, errors = parse_output(response.content)
        if output is not None:
            break
        messages = [
            *messages,
            ChatMessage(role="assistant", content=response.content or ""),
            ChatMessage(role="user", content=_REASK[language] + "\n".join(errors)),
        ]

    findings = [
        f for f in (output.findings if output else []) if all(c in known for c in f.cite_ids)
    ]
    return {"findings": findings}
