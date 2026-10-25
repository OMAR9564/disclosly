"""retriever_simple: evidence for a single-fact question (research §R2, US4).

The fact lookups the router named (each ticker over its periods) run first without a model
call; when none matches, one document search with the question runs instead. Then at most
two `cheap` tool-calling turns (`research_turns`) gather the rest, keeping one call for the
writer. An advice question gets the Phase 3 advice note and no lookups. A limit hit keeps
the evidence gathered so far.
"""

from typing import Any

from agent.answer import Language
from agent.budget import LimitReached, take_tool_call
from agent.graph.context import NodeContext
from agent.graph.state import ResearchState, RouterDecision
from agent.loop import ADVICE_NOTE, TOOL_LIMIT, Turns, research_turns
from agent.prompts import system_prompt
from llm.types import ChatMessage, ChatRequest, ChatResponse, TaskClass, ToolCall
from tools.facts import KNOWN_METRICS
from tools.registry import wrap
from tools.types import SeenItem

MAX_TURNS = 2
# Calls kept for the writer: the answer and one re-ask.
WRITER_RESERVE = 1
QUERY_MAX_CHARS = 200


def question_message(state: ResearchState, language: Language) -> str:
    question = state["question"]
    return f"{question}\n\n{ADVICE_NOTE[language]}" if state.get("refused") else question


def evidence_message(items: list[SeenItem]) -> str:
    return "\n".join(wrap(item) for item in items)


async def retriever_simple_node(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
    language: Language = state.get("language") or "tr"
    tools = ctx.tools()
    items: dict[str, SeenItem] = {}
    ids = iter(range(1, 1_000))

    async def dispatch(call: ToolCall) -> str:
        take_tool_call(ctx.usage, ctx.budget, ctx.node)
        result = await tools.dispatch(call, language)
        await ctx.emit("tool_called", {"tool": result.tool, "summary": result.summary})
        await ctx.emit("tool_result", {"tool": result.tool, "count": result.count})
        for item in result.items:
            items.setdefault(item.cite_id, item)
        return result.content

    await ctx.emit("step_started", {"step_id": None, "summary": state["question"][:200]})
    messages = [
        ChatMessage(role="system", content=system_prompt(language)),
        ChatMessage(role="user", content=question_message(state, language)),
    ]
    limit: str | None = None
    try:
        decision: RouterDecision | None = state.get("router")
        if decision is not None and decision.periods:
            for ticker in decision.tickers:
                facts = {
                    "ticker": ticker,
                    "metrics": decision.metrics or list(KNOWN_METRICS),
                    "periods": decision.periods,
                }
                await dispatch(
                    ToolCall(id=f"p{next(ids)}", name="get_financial_facts", arguments=facts)
                )
        if not items and not state.get("refused"):
            # No fact matched: search the documents with the question itself, so the turns
            # start from evidence even when the cheap model answers without a tool call.
            args: dict[str, Any] = {"query": state["question"][:QUERY_MAX_CHARS]}
            if decision is not None and decision.tickers:
                args["ticker"] = decision.tickers[0]
            await dispatch(ToolCall(id=f"p{next(ids)}", name="search_documents", arguments=args))
        if items:
            messages.append(
                ChatMessage(role="user", content=evidence_message(list(items.values())))
            )

        async def call(task_class: TaskClass, request: ChatRequest) -> ChatResponse:
            return await ctx.call(request, task_class)

        async def run_tools(calls: list[ToolCall]) -> None:
            for tool_call in calls:
                try:
                    content = await dispatch(tool_call)
                except LimitReached:
                    content = TOOL_LIMIT
                messages.append(
                    ChatMessage(role="tool", content=content, tool_call_id=tool_call.id)
                )

        turns = Turns(call, lambda: ctx.usage.calls_left(ctx.budget), run_tools, messages, language)
        await research_turns(
            turns,
            tools.schemas,
            reserve=WRITER_RESERVE,
            max_turns=MAX_TURNS,
            tool_schema_version=tools.tool_schema_version,
        )
    except LimitReached as exc:
        limit = exc.limit

    update: dict[str, Any] = {"evidence": list(items.values())}
    if limit is not None:
        update["limit_hit"] = limit
    return update
