"""The single-agent loop (research §R1, contracts/agent-answer.md, contracts/sse-events.md).

`cheap` calls with tools gather evidence; the last allowed model call is forced to produce the
`Answer` JSON with `strong`. Limits on model calls, tool calls and wall time are enforced here,
in code, never left to the model. Validation failure gets one re-ask with the errors only if a
model call is left; otherwise, or on a second failure, the run ends `partial` with the
validated points only.
"""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import ValidationError

from agent.advice import detect_language, is_advice_request
from agent.answer import (
    NO_VERIFIED_ANSWER,
    Answer,
    AnswerStatus,
    Language,
    Validation,
    answer_json_schema,
    finalize,
    normalize_markers,
    to_partial,
    validate_answer,
)
from agent.prompts import system_prompt
from agent.telemetry import tool_span
from llm.router import Router
from llm.types import ChatMessage, ChatRequest, ChatResponse, TaskClass, ToolCall, ToolSchema
from tools.registry import ToolResult
from tools.types import SeenItem

EXCERPT_MAX_CHARS = 1500

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
Clock = Callable[[], float]


class Tools(Protocol):
    @property
    def schemas(self) -> list[ToolSchema]: ...

    async def dispatch(self, call: ToolCall, language: Language = "tr") -> ToolResult: ...


@dataclass(frozen=True)
class Budget:
    max_model_calls: int = 4
    max_tool_calls: int = 6
    deadline_seconds: float = 90.0


class DeadlineExceeded(Exception):
    pass


@dataclass
class RunOutcome:
    status: AnswerStatus
    answer: Answer
    citations: list[dict[str, Any]]
    usage: dict[str, Any]
    language: Language


@dataclass
class _State:
    budget: Budget
    clock: Clock
    started: float
    model_calls: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    providers: list[str] = field(default_factory=list)
    seen: dict[str, SeenItem] = field(default_factory=dict)

    def remaining_time(self) -> float:
        return self.budget.deadline_seconds - (self.clock() - self.started)

    def calls_left(self) -> int:
        return self.budget.max_model_calls - self.model_calls


_FINAL_INSTRUCTION = {
    "tr": "Şimdi son yanıtı yalnızca şemaya uyan tek bir JSON nesnesi olarak ver.",
    "en": "Now give the final answer as a single JSON object matching the schema, nothing else.",
}
_REASK = {
    "tr": "Yanıtın doğrulamadan geçmedi. Şu hataları düzelt ve JSON'u yeniden ver:\n",
    "en": "Your answer failed validation. Fix these errors and return the JSON again:\n",
}
_ADVICE_NOTE = {
    "tr": "Not: Bu soru yatırım tavsiyesi istiyor. `status` alanı `refused` olmalı; "
    "yalnızca belgelerdeki bilgileri ver.",
    "en": "Note: this question asks for investment advice. `status` must be `refused`; "
    "give only factual information from the documents.",
}
_TOOL_LIMIT = "Tool call limit reached. Answer with the evidence you have."
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def parse_answer(content: str | None) -> tuple[Answer | None, list[str]]:
    if not content:
        return None, ["empty response; return the Answer JSON"]
    try:
        return normalize_markers(Answer.model_validate_json(_FENCE.sub("", content.strip()))), []
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(p) for p in e['loc']) or 'answer'}: {e['msg']}"
            for e in exc.errors()[:10]
        ]


def _citations(answer: Answer, seen: dict[str, SeenItem]) -> list[dict[str, Any]]:
    ordered = [c.cite_id for c in answer.citations] + [c for p in answer.points for c in p.cite_ids]
    return [
        {
            "cite_id": cite_id,
            "excerpt": seen[cite_id].text[:EXCERPT_MAX_CHARS],
            "source": seen[cite_id].source,
        }
        for cite_id in dict.fromkeys(ordered)
        if cite_id in seen
    ]


ModelCall = Callable[[TaskClass, ChatRequest], Awaitable[ChatResponse]]
RunTools = Callable[[list[ToolCall]], Awaitable[None]]


@dataclass
class Turns:
    """The conversation the research and answer helpers share, with its accounting.

    `call` makes one model call within the run's limits (raising when none is left), and
    `run_tools` executes tool calls and appends their results to `messages`.
    """

    call: ModelCall
    calls_left: Callable[[], int]
    run_tools: RunTools
    messages: list[ChatMessage]
    language: Language


@dataclass
class AnswerResult:
    answer: Answer | None = None  # a validated answer
    last: tuple[Answer, Validation] | None = None  # the last answer that failed validation


async def research_turns(
    turns: Turns,
    schemas: list[ToolSchema],
    *,
    reserve: int = 1,
    max_turns: int | None = None,
    tool_schema_version: str = "1",
) -> str | None:
    """Cheap tool-calling turns while more than `reserve` calls are left.

    Returns the content of a turn that answered without tools (an answer candidate), or None
    when the turns ran out.
    """
    taken = 0
    while turns.calls_left() > reserve and (max_turns is None or taken < max_turns):
        taken += 1
        response = await turns.call(
            "cheap",
            ChatRequest(
                messages=turns.messages, tools=schemas, tool_schema_version=tool_schema_version
            ),
        )
        if not response.tool_calls:
            return response.content
        turns.messages.append(
            ChatMessage(role="assistant", content=response.content, tool_calls=response.tool_calls)
        )
        await turns.run_tools(response.tool_calls)
    return None


async def answer_turn(
    turns: Turns,
    seen: Mapping[str, SeenItem],
    candidate: str | None = None,
    result: AnswerResult | None = None,
) -> AnswerResult:
    """The forced `strong` Answer call, validated, with one re-ask while a call is left.

    `result` is filled as the turn goes, so a caller that catches a deadline mid-turn still
    has the last answer that failed validation.
    """
    language = turns.language
    result = result if result is not None else AnswerResult()
    reasked = False
    while True:
        if candidate is None:
            if turns.calls_left() < 1:
                break
            # A re-ask already ends with the errors; a first attempt gets the instruction.
            final = (
                [] if reasked else [ChatMessage(role="user", content=_FINAL_INSTRUCTION[language])]
            )
            response = await turns.call(
                "strong",
                ChatRequest(
                    messages=[*turns.messages, *final],
                    response_schema=answer_json_schema(),
                    max_tokens=2048,
                ),
            )
            candidate = response.content
        answer, errors = parse_answer(candidate)
        if answer is not None:
            validation = validate_answer(answer, seen, language)
            if validation.ok:
                result.answer = answer
                return result
            result.last = (answer, validation)
            errors = validation.errors
        if reasked or turns.calls_left() < 1:
            break
        turns.messages.append(ChatMessage(role="assistant", content=candidate or ""))
        turns.messages.append(
            ChatMessage(role="user", content=_REASK[language] + "\n".join(errors))
        )
        candidate = None
        reasked = True
    return result


async def run_agent(
    question: str,
    router: Router,
    tools: Tools,
    budget: Budget,
    emit: Emit,
    *,
    clock: Clock = time.monotonic,
) -> RunOutcome:
    language = detect_language(question)
    advice = is_advice_request(question)
    state = _State(budget=budget, clock=clock, started=clock())
    user = question if not advice else f"{question}\n\n{_ADVICE_NOTE[language]}"
    messages = [
        ChatMessage(role="system", content=system_prompt(language)),
        ChatMessage(role="user", content=user),
    ]

    async def call(task_class: TaskClass, request: ChatRequest) -> ChatResponse:
        remaining = state.remaining_time()
        if remaining <= 0:
            raise DeadlineExceeded
        state.model_calls += 1
        try:
            response = await asyncio.wait_for(
                router.chat(request, task_class=task_class), timeout=remaining
            )
        except TimeoutError:
            raise DeadlineExceeded from None
        state.tokens_in += response.tokens_in
        state.tokens_out += response.tokens_out
        state.providers.append(response.provider)
        await emit(
            "model_called",
            {
                "task_class": task_class,
                "provider": response.provider,
                "cache_hit": response.cache_hit,
                "fallback_from": ",".join(response.fallback_from) or None,
            },
        )
        return response

    async def run_tools(tool_calls: list[ToolCall]) -> None:
        for tool_call in tool_calls:
            if state.tool_calls >= budget.max_tool_calls:
                content = _TOOL_LIMIT
            else:
                state.tool_calls += 1
                with tool_span(name=tool_call.name):
                    result = await tools.dispatch(tool_call, language)
                await emit("tool_called", {"tool": result.tool, "summary": result.summary})
                await emit("tool_result", {"tool": result.tool, "count": result.count})
                state.seen.update((item.cite_id, item) for item in result.items)
                content = result.content
            messages.append(ChatMessage(role="tool", content=content, tool_call_id=tool_call.id))

    turns = Turns(call, state.calls_left, run_tools, messages, language)
    result = AnswerResult()
    deadline_hit = False
    try:
        # Research: cheap calls with tools, keeping one call for the final answer.
        candidate = await research_turns(turns, tools.schemas)
        # Answer: validate, re-ask at most once while a model call is left.
        await answer_turn(turns, state.seen, candidate, result)
        if result.answer is not None:
            return _outcome(result.answer, state, language, advice)
    except DeadlineExceeded:
        deadline_hit = True

    last = result.last

    if last is not None:
        partial = to_partial(last[0], last[1], language)
    else:
        partial = Answer(language=language, status="partial", summary=NO_VERIFIED_ANSWER[language])
    return _outcome(partial, state, language, advice, deadline_hit=deadline_hit, forced=True)


def _outcome(
    answer: Answer,
    state: _State,
    language: Language,
    advice: bool,
    *,
    deadline_hit: bool = False,
    forced: bool = False,
) -> RunOutcome:
    status: AnswerStatus = answer.status
    if advice:
        status = "refused"
    elif not forced and (not state.seen or answer.status == "no_evidence"):
        # Nothing citable came back: the company or topic is not covered.
        status = "no_evidence"
        answer = answer.model_copy(update={"points": [], "citations": []})
    answer = finalize(answer.model_copy(update={"status": status}))
    usage = {
        "model_calls": state.model_calls,
        "tool_calls": state.tool_calls,
        "tokens_in": state.tokens_in,
        "tokens_out": state.tokens_out,
        "providers": state.providers,
        "deadline_exceeded": deadline_hit,
    }
    return RunOutcome(status, answer, _citations(answer, state.seen), usage, language)
