"""retriever node (T021) with fake tools and a scripted model."""

import json
from decimal import Decimal
from typing import Any

from agent.budget import ModeBudget, Usage
from agent.graph.nodes.retriever import retriever_node
from agent.graph.state import ResearchState
from agent.plan import Plan
from llm.types import ChatResponse, ToolCall
from tests.agent_fakes import ScriptedRouter
from tests.unit.agent.graph.helpers import Events, make_ctx, make_deps
from tools.registry import ToolResult, wrap
from tools.types import SeenItem


def _fact(n: int, ticker: str) -> SeenItem:
    return SeenItem(
        cite_id=f"f:{n}",
        text=f"FAVÖK (2025-Q4): {n},5 milyar TRY",
        value=Decimal(f"{n}.5"),
        source={"kind": "fact", "ticker": ticker},
    )


def _chunk(cid: str, text: str) -> SeenItem:
    return SeenItem(cite_id=f"c:{cid}", text=text, source={"kind": "chunk"})


class Toolbox:
    """Answers each tool from fixtures; records calls. Tickers outside `covered` have no docs."""

    def __init__(
        self,
        covered: set[str],
        facts: dict[str, list[SeenItem]] | None = None,
        searches: dict[str, list[SeenItem]] | None = None,
    ) -> None:
        self.covered = covered
        self.facts = facts or {}
        self.searches = searches or {}
        self.calls: list[ToolCall] = []

    def for_node(self, node: str) -> "Toolbox":
        return self

    async def dispatch(self, call: ToolCall, language: str = "tr") -> ToolResult:
        self.calls.append(call)
        args = call.arguments
        if call.name == "list_documents":
            count = 1 if args["ticker"] in self.covered else 0
            return ToolResult(call.name, "docs", f"{args['ticker']} documents", count=count)
        if call.name == "get_financial_facts":
            items = self.facts.get(args["ticker"], [])
        else:
            items = self.searches.get(args["query"], [])
        content = "\n".join(wrap(i) for i in items) or "No results."
        return ToolResult(call.name, content, f"{call.name} summary", items, len(items))

    def names(self) -> list[str]:
        return [c.name for c in self.calls]


def _plan(*steps: dict[str, Any]) -> Plan:
    return Plan.model_validate({"steps": list(steps)})


FACTS_STEP = {
    "id": "s1",
    "kind": "facts",
    "goal": "Net borç ve FAVÖK",
    "tickers": ["THYAO", "PGSUS"],
    "periods": ["2025-Q4", "2025-Q1"],
    "metrics": ["ebitda"],
}
SEARCH_STEP = {
    "id": "s2",
    "kind": "search",
    "goal": "THYAO yakıt riskleri",
    "tickers": ["THYAO"],
    "periods": ["2025-FY", "2025-Q2"],
}


def _verdicts(*verdicts: dict[str, Any]) -> ChatResponse:
    return ChatResponse(content=json.dumps({"steps": list(verdicts)}), provider="groq", model="m")


def _state(plan: Plan) -> ResearchState:
    return {
        "question": "THYAO ve PGSUS",
        "language": "tr",
        "plan": plan,
        "plan_approved": True,
        "budget": ModeBudget.deep(),
        "usage": Usage(model_calls=2),
    }


async def _run(
    toolbox: Toolbox, router: ScriptedRouter, plan: Plan, budget: ModeBudget | None = None
) -> tuple[dict[str, Any], Events, Any]:
    events = Events()
    deps = make_deps(router, events)
    deps.tools = toolbox  # type: ignore[assignment]
    ctx = make_ctx(
        deps, "retriever", budget=budget or ModeBudget.deep(), usage=Usage(model_calls=2)
    )
    return await retriever_node(_state(plan), ctx), events, ctx


async def test_facts_and_search_without_a_model_call_then_one_judgment() -> None:
    toolbox = Toolbox(
        {"THYAO", "PGSUS"},
        facts={"THYAO": [_fact(1, "THYAO")], "PGSUS": [_fact(2, "PGSUS")]},
        searches={"THYAO yakıt riskleri": [_chunk("a", "Yakıt fiyatları risk oluşturuyor.")]},
    )
    router = ScriptedRouter(
        [_verdicts({"step_id": "s1", "sufficient": True}, {"step_id": "s2", "sufficient": True})]
    )

    update, events, ctx = await _run(toolbox, router, _plan(FACTS_STEP, SEARCH_STEP))

    assert [i.cite_id for i in update["evidence"]] == ["f:1", "f:2", "c:a"]
    assert [s.status for s in update["plan"].steps] == ["done", "done"]
    assert "limit_hit" not in update
    assert len(router.calls) == 1
    assert router.calls[0][0] == "cheap"
    judged = router.calls[0][1].messages[1].content or ""
    assert '<untrusted_document cite_id="c:a">' in judged
    facts_call = next(c for c in toolbox.calls if c.name == "get_financial_facts")
    assert facts_call.arguments == {
        "ticker": "THYAO",
        "metrics": ["ebitda"],
        "periods": ["2025-Q4", "2025-Q1"],
    }
    search_call = next(c for c in toolbox.calls if c.name == "search_documents")
    assert (search_call.arguments["period_from"], search_call.arguments["period_to"]) == (
        "2025-Q2",
        "2025-FY",
    )
    assert ctx.usage.tool_calls == len(toolbox.calls) == 5
    assert events.data("step_started")[0] == {
        "step_id": "s1",
        "summary": "Net borç ve FAVÖK",
        "node": "retriever",
        "attempt": 1,
    }
    assert [d["step_id"] for d in events.data("tool_called")][:2] == ["s1", "s1"]
    assert [(d["step_id"], d["status"]) for d in events.data("step_finished")] == [
        ("s1", "done"),
        ("s2", "done"),
    ]


async def test_ticker_not_in_the_corpus_is_not_covered() -> None:
    toolbox = Toolbox({"THYAO"}, facts={"THYAO": [_fact(1, "THYAO")]})
    step = {**FACTS_STEP, "tickers": ["ZZZZ"]}
    router = ScriptedRouter([_verdicts({"step_id": "s2", "sufficient": True})])
    update, _, _ = await _run(
        toolbox, router, _plan(step, {**FACTS_STEP, "id": "s2", "tickers": ["THYAO", "ZZZZ"]})
    )

    assert [s.status for s in update["plan"].steps] == ["not_covered", "done"]
    # Coverage is looked up once per ticker; the uncovered one gets no fact lookup.
    assert toolbox.names().count("list_documents") == 2
    assert [c.arguments["ticker"] for c in toolbox.calls if c.name == "get_financial_facts"] == [
        "THYAO"
    ]


async def test_rewrites_stop_after_two_rounds() -> None:
    toolbox = Toolbox({"THYAO"})
    insufficient = {"step_id": "s2", "sufficient": False, "rewritten_query": "jet yakıtı hedge"}
    router = ScriptedRouter([_verdicts(insufficient), _verdicts(insufficient), _verdicts()])

    update, _, ctx = await _run(toolbox, router, _plan(SEARCH_STEP))

    assert len(router.calls) == 2  # one judgment per rewrite round
    queries = [c.arguments["query"] for c in toolbox.calls if c.name == "search_documents"]
    assert queries == ["THYAO yakıt riskleri", "jet yakıtı hedge", "jet yakıtı hedge"]
    assert update["plan"].steps[0].status == "failed"
    assert ctx.usage.model_calls == 4


async def test_rewrite_finds_evidence() -> None:
    toolbox = Toolbox(
        {"THYAO"}, searches={"jet yakıtı hedge": [_chunk("h", "Yakıt hedge oranı yükseldi.")]}
    )
    router = ScriptedRouter(
        [
            _verdicts(
                {"step_id": "s2", "sufficient": False, "rewritten_query": "jet yakıtı hedge"}
            ),
            _verdicts({"step_id": "s2", "sufficient": True}),
        ]
    )
    update, _, _ = await _run(toolbox, router, _plan(SEARCH_STEP))
    assert update["plan"].steps[0].status == "done"
    assert [i.cite_id for i in update["evidence"]] == ["c:h"]


async def test_facts_steps_are_never_rewritten_and_garbage_verdicts_end_rounds() -> None:
    toolbox = Toolbox({"THYAO", "PGSUS"})
    router = ScriptedRouter(
        [
            _verdicts({"step_id": "s1", "sufficient": False, "rewritten_query": "x"}),
            ChatResponse(content="not json", provider="g", model="m"),
        ]
    )
    update, _, _ = await _run(toolbox, router, _plan(FACTS_STEP))
    assert len(router.calls) == 1
    assert update["plan"].steps[0].status == "failed"


async def test_tool_budget_exhausted_keeps_evidence_and_sets_limit_hit() -> None:
    toolbox = Toolbox(
        {"THYAO", "PGSUS"}, facts={"THYAO": [_fact(1, "THYAO")], "PGSUS": [_fact(2, "PGSUS")]}
    )
    budget = ModeBudget(mode="deep", max_model_calls=20, deadline_seconds=300, max_tool_calls=2)
    update, _, ctx = await _run(toolbox, ScriptedRouter([]), _plan(FACTS_STEP), budget)

    assert update["limit_hit"] == "tool_calls"
    assert ctx.usage.tool_calls == 2
    assert update["evidence"] == []
    assert update["plan"].steps[0].status == "pending"


async def test_no_judgment_without_spare_calls() -> None:
    toolbox = Toolbox({"THYAO"}, searches={"THYAO yakıt riskleri": [_chunk("a", "x")]})
    budget = ModeBudget(mode="deep", max_model_calls=5, deadline_seconds=300, max_tool_calls=24)
    router = ScriptedRouter([])
    update, _, _ = await _run(toolbox, router, _plan(SEARCH_STEP), budget)
    assert router.calls == []
    assert update["plan"].steps[0].status == "done"


async def test_calculate_step_needs_no_tools_and_no_plan_means_nothing() -> None:
    toolbox = Toolbox({"THYAO"})
    step = {"id": "s1", "kind": "calculate", "goal": "Oran", "tickers": ["THYAO"]}
    update, _, _ = await _run(toolbox, ScriptedRouter([_verdicts()]), _plan(step))
    assert update["plan"].steps[0].status == "done"
    assert toolbox.names() == ["list_documents"]

    ctx = make_ctx(make_deps(ScriptedRouter([])), "retriever")
    assert await retriever_node({"question": "q"}, ctx) == {}


async def test_a_facts_step_without_metrics_searches_the_text() -> None:
    goal = "Apple brüt marjı"
    step = {"id": "s1", "kind": "facts", "goal": goal, "tickers": ["THYAO"], "periods": ["2025-Q4"]}
    toolbox = Toolbox({"THYAO"}, searches={goal: [_chunk("g", "Gross margin was 47.1%.")]})
    router = ScriptedRouter([_verdicts({"step_id": "s1", "sufficient": True})])

    update, _, _ = await _run(toolbox, router, _plan(step))

    assert "get_financial_facts" not in toolbox.names()
    assert [i.cite_id for i in update["evidence"]] == ["c:g"]
    assert update["plan"].steps[0].status == "done"
