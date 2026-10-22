"""Router node: language, advice check, then one `cheap` call for mode and entities (§R2).

Language detection and the advice check run in code first; an advice question is refused
without a model call. The model returns a `RouterDecision`; an invalid one is re-asked once,
and when that fails too the run takes the simple path. Simple runs get their deadline here;
deep runs get theirs when the plan is approved.
"""

import re
from datetime import timedelta
from typing import Any

from pydantic import ValidationError

from agent.advice import detect_language, is_advice_request
from agent.answer import Language
from agent.budget import ModeBudget
from agent.graph.context import NodeContext
from agent.graph.state import ResearchState, RouterDecision
from agent.prompts import node_prompt, with_schema
from llm.types import ChatMessage, ChatRequest
from tools.facts import KNOWN_METRICS

MAX_TOKENS = 512
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_PERIOD = re.compile(r"^\d{4}-(Q[1-4]|FY)$")

_REASK = {
    "tr": "Yanıtın şemaya uymadı. Şu hataları düzelt ve yalnızca JSON'u yeniden ver:\n",
    "en": "Your reply did not match the schema. Fix these errors and return only the JSON:\n",
}


def parse_decision(content: str | None) -> tuple[RouterDecision | None, list[str]]:
    if not content:
        return None, ["empty response; return the RouterDecision JSON"]
    try:
        return RouterDecision.model_validate_json(_FENCE.sub("", content.strip())), []
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(p) for p in e['loc']) or 'decision'}: {e['msg']}"
            for e in exc.errors(include_input=False)[:10]
        ]


def names_ticker(question: str, ticker: str) -> bool:
    """True when the question itself contains `ticker` as a word."""
    pattern = rf"(?<![A-Z0-9]){re.escape(ticker.upper())}(?![A-Z0-9])"
    return re.search(pattern, question.upper()) is not None


def clean(decision: RouterDecision, question: str) -> RouterDecision:
    """Keep what the next steps can use: known metrics, well-formed periods, and a memory
    change only for a ticker the user's own question names."""
    intent = decision.memory_intent
    if intent is not None and not names_ticker(question, intent.ticker):
        intent = None
    return decision.model_copy(
        update={
            "metrics": [m for m in dict.fromkeys(decision.metrics) if m in KNOWN_METRICS],
            "periods": [p for p in dict.fromkeys(decision.periods) if _PERIOD.match(p)],
            "memory_intent": intent,
        }
    )


async def _decide(
    question: str, language: Language, ctx: NodeContext, budget: ModeBudget
) -> RouterDecision:
    system = with_schema(node_prompt("router", language), RouterDecision.model_json_schema())
    messages = [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=question),
    ]
    schema = RouterDecision.model_json_schema()
    for attempt in range(2):
        if attempt and ctx.usage.calls_left(budget) < 1:
            break
        response = await ctx.call(
            ChatRequest(messages=messages, response_schema=schema, max_tokens=MAX_TOKENS),
            "cheap",
            budget=budget,
        )
        decision, errors = parse_decision(response.content)
        if decision is not None:
            return decision
        messages = [
            *messages,
            ChatMessage(role="assistant", content=response.content or ""),
            ChatMessage(role="user", content=_REASK[language] + "\n".join(errors)),
        ]
    return RouterDecision(mode="simple")


async def router_node(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
    question = state["question"]
    language = detect_language(question)
    simple = ctx.deps.budgets["simple"]
    started = state.get("submitted_at") or ctx.deps.clock()
    simple_deadline = started + timedelta(seconds=simple.deadline_seconds)

    if is_advice_request(question):
        await ctx.emit("mode_selected", {"mode": "refused", "uses_watchlist": False})
        return {
            "language": language,
            "mode": "simple",
            "refused": True,
            "router": None,
            "budget": simple,
            "deadline_at": simple_deadline,
        }

    # The router call is bounded by the simple deadline whichever mode it picks.
    ctx.deadline_at = simple_deadline
    decision = clean(await _decide(question, language, ctx, simple), question)
    await ctx.emit(
        "mode_selected", {"mode": decision.mode, "uses_watchlist": decision.uses_watchlist}
    )
    deep = decision.mode == "deep"
    return {
        "language": language,
        "mode": decision.mode,
        "refused": False,
        "router": decision,
        "budget": ctx.deps.budgets["deep"] if deep else simple,
        "deadline_at": None if deep else simple_deadline,
    }
