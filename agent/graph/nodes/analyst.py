"""analyst: one call that turns the gathered evidence into cited findings and calculations.

Evidence reaches the model wrapped as untrusted documents. The reply must match
`AnalystOutput`; an invalid reply is re-asked once while a call is left. Findings citing an id
that is not in the evidence are dropped. Calculations (research §R9) get their input values in
code (agent/graph/calculations.py) and run together in one sandbox execution; each becomes a
`Calculation` with a `k:<n>` id and a `calculation` event. The task class is `strong` when the
plan has a `calculate` step.
"""

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.answer import Language
from agent.graph.calculations import (
    MAX_CALCULATIONS,
    CalculationSpec,
    Resolved,
    batch,
    batch_errors,
    fits,
    resolve,
    to_calculation,
)
from agent.graph.context import NodeContext
from agent.graph.state import Calculation, Finding, ResearchState
from agent.plan import Plan
from agent.prompts import node_prompt, with_schema
from llm.types import ChatMessage, ChatRequest, TaskClass
from tools.python import SandboxRejected, run_python
from tools.registry import wrap
from tools.types import SandboxUnavailable, SeenItem

MAX_FINDINGS = 30
MAX_EVIDENCE_ITEMS = 60
EVIDENCE_ITEM_CHARS = 1500
# A deep plan with several calculate steps writes code for each: 2048 tokens cut it off.
MAX_TOKENS = 4096
ANALYST_FAILED = "analyst: no valid output"
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_REASK = {
    "tr": "Yanıtın şemaya uymadı. Şu hataları düzelt ve yalnızca JSON'u yeniden ver:\n",
    "en": "Your reply did not match the schema. Fix these errors and return only the JSON:\n",
}


class AnalystOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[Finding] = Field(max_length=MAX_FINDINGS)
    calculations: list[CalculationSpec] = Field(default_factory=list, max_length=MAX_CALCULATIONS)


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

    if output is None:
        # The writer must not read "no findings" as "no evidence": it leaves the report to
        # finalize_partial.
        return {"findings": [], "calculations": [], "errors": [ANALYST_FAILED]}
    findings = [f for f in output.findings if all(c in known for c in f.cite_ids)]
    specs = output.calculations
    calculations = await run_calculations(state, ctx, specs) if specs else []
    return {"findings": findings, "calculations": calculations}


def _runnable(resolved: list[Resolved]) -> tuple[list[Resolved], dict[str, str]]:
    """The calculations that fit one batch; the rest fail with a reason."""
    kept: list[Resolved] = []
    dropped: dict[str, str] = {}
    for item in resolved:
        if item.error is not None:
            continue
        code, _ = batch([(r.spec.name, r.spec.code, r.inputs) for r in [*kept, item]])
        if fits(code):
            kept.append(item)
        else:
            dropped[item.spec.name] = "the calculations of one round exceed 16 KiB of code"
    return kept, dropped


async def run_calculations(
    state: ResearchState, ctx: NodeContext, specs: list[CalculationSpec]
) -> list[Calculation]:
    """Resolve inputs, run every runnable calculation in one sandbox call, record each one."""
    evidence = {item.cite_id: item for item in state.get("evidence") or []}
    resolved = resolve(specs, evidence)
    runnable, errors = _runnable(resolved)
    outputs = {}
    run_error: str | None = None
    if runnable:
        if ctx.deps.sandbox is None:
            raise SandboxUnavailable("no calculation sandbox is configured")
        code, values = batch([(r.spec.name, r.spec.code, r.inputs) for r in runnable])
        try:
            result = await run_python(
                ctx.deps.sandbox,
                code,
                values,
                usage=ctx.usage,
                budget=ctx.budget,
                deadline_at=ctx.deadline_at,
                clock=ctx.deps.clock,
            )
        except SandboxRejected as exc:
            run_error = str(exc)
        else:
            outputs = result.outputs
            errors = {**batch_errors(result.stdout), **errors}
            if not result.ok:
                run_error = result.error or "the sandbox run failed"
    first = len(state.get("calculations") or []) + 1
    calculations = [
        to_calculation(item, f"k:{first + index}", outputs, errors, run_error)
        for index, item in enumerate(resolved)
    ]
    for calc in calculations:
        await ctx.emit(
            "calculation",
            {
                "cite_id": calc.cite_id,
                "name": calc.name,
                "description": calc.description,
                "status": "ok" if calc.error is None else "error",
            },
        )
    return calculations
