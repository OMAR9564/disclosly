"""retriever: run the approved plan's lookups, then judge sufficiency once per round (§R2).

Every step's fact lookups and searches run through the node's tool set without a model call.
A step whose companies have no documents in the corpus is `not_covered`. One `cheap` call then
judges all steps at once and may rewrite the query of an insufficient `search` step; at most
two rewrite rounds run, within the tool-call budget. A limit hit keeps the evidence gathered
so far.
"""

import itertools
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.answer import Language
from agent.budget import LimitReached, take_tool_call
from agent.graph.context import NodeContext
from agent.graph.state import ResearchState
from agent.plan import Plan, PlanStep, StepStatus
from agent.prompts import node_prompt, with_schema
from llm.types import ChatMessage, ChatRequest, ToolCall
from tools.facts import KNOWN_METRICS
from tools.registry import ToolResult, wrap
from tools.types import SeenItem

MAX_REWRITES = 2
# Calls kept for the analyst, the verifier and the writer when deciding to judge.
RESERVED_CALLS = 3
EXCERPTS_PER_STEP = 3
EXCERPT_CHARS = 300
MAX_TOKENS = 800
QUERY_MAX_CHARS = 200
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


class StepVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    sufficient: bool
    rewritten_query: str | None = Field(default=None, max_length=QUERY_MAX_CHARS)


class Sufficiency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[StepVerdict]


def _period_key(period: str) -> tuple[int, int]:
    year, _, part = period.partition("-")
    return int(year), 4 if part == "FY" else int(part[1:])


class _Run:
    """One retriever execution: tool calls, evidence per step, statuses."""

    def __init__(self, state: ResearchState, ctx: NodeContext) -> None:
        self.ctx = ctx
        self.language: Language = state.get("language") or "tr"
        self.ids = itertools.count(1)
        self.items: dict[str, SeenItem] = {}
        self.by_step: dict[str, list[str]] = {}
        self.covered: dict[str, bool] = {}

    async def tool(self, step_id: str, name: str, args: dict[str, Any]) -> ToolResult:
        ctx = self.ctx
        take_tool_call(ctx.usage, ctx.budget, ctx.node)
        call = ToolCall(id=f"r{next(self.ids)}", name=name, arguments=args)
        result = await ctx.tools().dispatch(call, self.language)
        await ctx.emit("tool_called", {"tool": name, "summary": result.summary, "step_id": step_id})
        await ctx.emit("tool_result", {"tool": name, "count": result.count, "step_id": step_id})
        for item in result.items:
            self.items.setdefault(item.cite_id, item)
            self.by_step.setdefault(step_id, []).append(item.cite_id)
        return result

    async def is_covered(self, step_id: str, ticker: str) -> bool:
        if ticker not in self.covered:
            result = await self.tool(step_id, "list_documents", {"ticker": ticker})
            self.covered[ticker] = result.error is None and result.count > 0
        return self.covered[ticker]

    async def search(self, step: PlanStep, query: str, tickers: list[str]) -> None:
        args: dict[str, Any] = {"query": query[:QUERY_MAX_CHARS]}
        if step.periods:
            ordered = sorted(step.periods, key=_period_key)
            args |= {"period_from": ordered[0], "period_to": ordered[-1]}
        targets: list[str | None] = [*tickers] if tickers else [None]
        for ticker in targets:
            await self.tool(
                step.id, "search_documents", args | ({"ticker": ticker} if ticker else {})
            )

    async def run_step(self, step: PlanStep) -> StepStatus:
        await self.ctx.emit("step_started", {"step_id": step.id, "summary": step.goal})
        tickers = [t for t in step.tickers if await self.is_covered(step.id, t)]
        if step.tickers and not tickers:
            return "not_covered"
        if step.kind == "calculate":
            return "done"  # computed by the analyst from the other steps' figures
        if step.kind == "facts" and step.periods:
            metrics = step.metrics or list(KNOWN_METRICS)
            for ticker in tickers:
                await self.tool(
                    step.id,
                    "get_financial_facts",
                    {"ticker": ticker, "metrics": metrics, "periods": step.periods},
                )
        else:
            await self.search(step, step.goal, tickers)
        return "done" if self.by_step.get(step.id) else "failed"

    def evidence_block(self, steps: list[PlanStep]) -> str:
        parts = []
        for step in steps:
            ids = self.by_step.get(step.id, [])
            shown = [self.items[c] for c in ids[:EXCERPTS_PER_STEP]]
            excerpts = "\n".join(
                wrap(item.model_copy(update={"text": item.text[:EXCERPT_CHARS]})) for item in shown
            )
            header = {"step_id": step.id, "kind": step.kind, "goal": step.goal, "items": len(ids)}
            parts.append(
                json.dumps(header, ensure_ascii=False) + ("\n" + excerpts if shown else "")
            )
        return "\n\n".join(parts)

    async def judge(self, question: str, steps: list[PlanStep]) -> list[StepVerdict] | None:
        schema = Sufficiency.model_json_schema()
        system = with_schema(node_prompt("retriever", self.language), schema)
        user = f"{question}\n\n{self.evidence_block(steps)}"
        response = await self.ctx.call(
            ChatRequest(
                messages=[
                    ChatMessage(role="system", content=system),
                    ChatMessage(role="user", content=user),
                ],
                response_schema=schema,
                max_tokens=MAX_TOKENS,
            ),
            "cheap",
        )
        try:
            verdicts = Sufficiency.model_validate_json(
                _FENCE.sub("", (response.content or "").strip())
            )
        except ValidationError:
            return None
        return verdicts.steps


async def retriever_node(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
    plan: Plan | None = state.get("plan")
    if plan is None:
        return {}
    run = _Run(state, ctx)
    statuses: dict[str, StepStatus] = {}
    limit: str | None = None
    try:
        for step in plan.steps:
            statuses[step.id] = await run.run_step(step)
            await ctx.emit("step_finished", {"step_id": step.id, "status": statuses[step.id]})

        by_id = {step.id: step for step in plan.steps}
        for _ in range(MAX_REWRITES):
            active = [s for s in plan.steps if statuses[s.id] in ("done", "failed")]
            if not active or ctx.usage.calls_left(ctx.budget) <= RESERVED_CALLS:
                break
            verdicts = await run.judge(state["question"], active)
            rewrites = [
                v
                for v in verdicts or []
                if not v.sufficient
                and v.rewritten_query
                and v.step_id in by_id
                and by_id[v.step_id].kind == "search"
                and statuses[v.step_id] != "not_covered"
            ]
            if not rewrites:
                break
            for verdict in rewrites:
                step = by_id[verdict.step_id]
                tickers = [t for t in step.tickers if run.covered.get(t)]
                await run.search(step, verdict.rewritten_query or step.goal, tickers)
                if run.by_step.get(step.id):
                    statuses[step.id] = "done"
    except LimitReached as exc:
        limit = exc.limit

    steps = [s.model_copy(update={"status": statuses.get(s.id, s.status)}) for s in plan.steps]
    update: dict[str, Any] = {
        "plan": plan.model_copy(update={"steps": steps}),
        "evidence": list(run.items.values()),
    }
    if limit is not None:
        update["limit_hit"] = limit
    return update
