"""The simple path (T043): router → retriever_simple → writer_simple within 4 model calls."""

import json
import uuid
from typing import Any, cast

from langgraph.checkpoint.memory import InMemorySaver

from agent.checkpoint import graph_serde
from agent.graph.build import DURABILITY, build_graph
from agent.graph.state import ResearchState
from agent.report import Report
from llm.types import ChatResponse, ToolCall
from tests.agent_fakes import (
    FACT,
    FACTS_CALL,
    GOOD,
    FakeTools,
    ScriptedRouter,
    answer_response,
    tool_response,
)
from tests.unit.agent.graph.helpers import Events, make_deps
from tools.registry import GRAPH_TOOL_SCHEMA_VERSION

QUESTION = "THYAO 2025 3. çeyrek net kârı ne kadar?"


class Source:
    """Every node gets the same fake tools."""

    def __init__(self, tools: FakeTools) -> None:
        self.tools = tools
        self.tool_schema_version = GRAPH_TOOL_SCHEMA_VERSION

    def for_node(self, node: Any) -> "Source":
        return self

    @property
    def schemas(self) -> Any:
        return self.tools.schemas

    async def dispatch(self, call: ToolCall, language: str = "tr") -> Any:
        return await self.tools.dispatch(call, language)


def _router(**fields: Any) -> ChatResponse:
    body = {"mode": "simple", **fields}
    return ChatResponse(content=json.dumps(body), provider="groq", model="m")


def _text(content: str) -> ChatResponse:
    return ChatResponse(content=content, provider="groq", model="m")


async def _run(
    router: ScriptedRouter, tools: FakeTools, question: str = QUESTION
) -> tuple[Report, dict[str, Any], Events]:
    events = Events()
    deps = make_deps(router, events)
    deps.tools = Source(tools)  # type: ignore[assignment]
    graph = build_graph(deps, InMemorySaver(serde=graph_serde()))
    config: Any = {"configurable": {"thread_id": str(uuid.uuid4())}}
    initial = {"run_id": "run-1", "user_id": "u", "question": question, "errors": []}
    result = await graph.ainvoke(cast(ResearchState, initial), config, durability=DURABILITY)
    draft = result["draft"]
    assert isinstance(draft, Report)
    return draft, dict(result), events


def _systems(router: ScriptedRouter) -> list[str]:
    return [request.messages[0].content or "" for _, request in router.calls]


async def test_router_facts_are_fetched_without_a_model_call() -> None:
    router = ScriptedRouter(
        [
            _router(tickers=["THYAO"], periods=["2025-Q3"], metrics=["net_income"]),
            _text("Kanıt yeterli."),
            answer_response(**GOOD),
        ]
    )
    tools = FakeTools({"get_financial_facts": [FACT]})
    report, state, events = await _run(router, tools)

    assert report.status == "completed"
    assert report.cite_ids() == ["f:1"]
    assert [c.name for c in tools.calls] == ["get_financial_facts"]
    assert tools.calls[0].arguments == {
        "ticker": "THYAO",
        "metrics": ["net_income"],
        "periods": ["2025-Q3"],
    }
    assert [task for task, _ in router.calls] == ["cheap", "cheap", "strong"]
    assert state["usage"].model_calls == 3
    assert "planner" not in state["usage"].nodes
    assert "tool_called" in events.names()


async def test_tool_turns_stop_at_four_calls_with_the_router() -> None:
    router = ScriptedRouter(
        [
            _router(),
            tool_response(*FACTS_CALL, call_id="a"),
            tool_response(*FACTS_CALL, call_id="b"),
            answer_response(**GOOD),
        ]
    )
    tools = FakeTools({"get_financial_facts": [FACT]})
    report, state, _ = await _run(router, tools)

    assert report.status == "completed"
    assert [task for task, _ in router.calls] == ["cheap", "cheap", "cheap", "strong"]
    assert state["usage"].model_calls == 4
    # No router facts: one search with the question first, then the two turns' lookups.
    assert [c.name for c in tools.calls] == ["search_documents", *["get_financial_facts"] * 2]
    assert tools.calls[0].arguments == {"query": QUESTION}


async def test_failed_answer_is_reasked_once_then_partial() -> None:
    bad = {**GOOD, "points": [{"text": "Net kâr 9.999 milyon TL", "cite_ids": ["f:1"]}]}
    router = ScriptedRouter(
        [
            _router(tickers=["THYAO"], periods=["2025-Q3"]),
            _text("Tamam."),
            answer_response(**bad),
            answer_response(**bad),
        ]
    )
    report, state, _ = await _run(router, FakeTools({"get_financial_facts": [FACT]}))

    assert report.status == "partial"
    assert state["usage"].model_calls == 4


async def test_advice_question_is_refused_without_a_router_or_planner_call() -> None:
    refused = {"status": "refused", "summary": "Yatırım tavsiyesi vermiyorum.", "points": []}
    router = ScriptedRouter([_text("Tamam."), answer_response(**refused)])
    tools = FakeTools()
    report, state, events = await _run(router, tools, "THYAO hissesi almalı mıyım?")

    assert report.status == "refused"
    assert tools.calls == []
    assert len(router.calls) == 2
    assert all("planlayıcı" not in s and "yönlendirici" not in s for s in _systems(router))
    assert "Not: Bu soru yatırım tavsiyesi istiyor" in (
        router.calls[0][1].messages[1].content or ""
    )
    assert events.data("mode_selected")[0]["mode"] == "refused"
    assert state["usage"].model_calls <= 4


async def test_no_evidence_skips_the_writer_call() -> None:
    router = ScriptedRouter([_router(tickers=["ZZZZ"]), _text("Bilgi yok.")])
    tools = FakeTools()
    report, _, _ = await _run(router, tools, "ZZZZ net kârı ne kadar?")

    assert tools.calls[0].arguments == {"query": "ZZZZ net kârı ne kadar?", "ticker": "ZZZZ"}
    assert report.status == "no_evidence"
    assert report.points == []
    assert [task for task, _ in router.calls] == ["cheap", "cheap"]
