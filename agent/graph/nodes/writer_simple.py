"""writer_simple: the Phase 3 answer for a simple run, wrapped as a `Report` (US4).

`answer_turn` makes the `strong` Answer call over the question and the gathered evidence,
with the Phase 3 inline citation check (without model-derived percentages, FR-008) and one
re-ask. An answer that still fails keeps only
its checked points (`to_partial`). An advice question ends `refused`; a run without evidence
gets a `no_evidence` report without a model call.
"""

from typing import Any

from agent.answer import Answer, Language, to_partial
from agent.budget import LimitReached
from agent.graph.context import NodeContext
from agent.graph.nodes.memory import with_memory_note
from agent.graph.nodes.retriever_simple import evidence_message, question_message
from agent.graph.nodes.writer import NO_EVIDENCE
from agent.graph.state import ResearchState
from agent.loop import AnswerResult, Turns, answer_turn
from agent.prompts import system_prompt
from agent.report import Report, finalize_report
from llm.types import ChatMessage, ChatRequest, ChatResponse, TaskClass, ToolCall


async def writer_simple_node(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
    language: Language = state.get("language") or "tr"
    refused = bool(state.get("refused"))
    evidence = {item.cite_id: item for item in state.get("evidence") or []}
    if not evidence and not refused:
        empty = Report(language=language, status="no_evidence", summary=NO_EVIDENCE[language])
        return {"draft": finalize_report(with_memory_note(empty, state))}

    messages = [
        ChatMessage(role="system", content=system_prompt(language)),
        ChatMessage(role="user", content=question_message(state, language)),
    ]
    if evidence:
        messages.append(ChatMessage(role="user", content=evidence_message(list(evidence.values()))))

    async def call(task_class: TaskClass, request: ChatRequest) -> ChatResponse:
        return await ctx.call(request, task_class)

    async def no_tools(calls: list[ToolCall]) -> None:
        return None

    turns = Turns(call, lambda: ctx.usage.calls_left(ctx.budget), no_tools, messages, language)
    result = AnswerResult()
    limit: str | None = None
    try:
        # FR-008: no sandbox on this path, so no model-derived numbers either.
        await answer_turn(turns, evidence, None, result, derived_ok=False)
    except LimitReached as exc:
        limit = exc.limit

    answer: Answer | None = result.answer
    if answer is None and result.last is not None:
        answer = to_partial(result.last[0], result.last[1], language)
    update: dict[str, Any] = {}
    if limit is not None:
        update["limit_hit"] = limit
    if answer is None:
        return {**update, "draft": None}
    if refused:
        answer = answer.model_copy(update={"status": "refused"})
    elif answer.status == "no_evidence":
        answer = answer.model_copy(update={"points": [], "citations": []})
    report = with_memory_note(Report.from_answer(answer), state)
    return {**update, "draft": finalize_report(report)}
