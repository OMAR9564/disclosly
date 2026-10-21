"""Tests for agent.loop (T017) with scripted and replayed model responses."""

import json
from typing import Any

import pytest

from agent.answer import DISCLAIMER
from agent.loop import Budget, run_agent
from llm.testing import FakeRouter
from llm.types import ChatResponse, ToolCall
from tests.agent_fakes import (
    FACT,
    FACTS_CALL,
    GOOD,
    POISON,
    FakeTools,
    ScriptedRouter,
    answer_response,
    tool_response,
)


class Events:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, event: str, data: dict[str, Any]) -> None:
        self.events.append((event, data))

    def names(self) -> list[str]:
        return [e for e, _ in self.events]


QUESTION = "THYAO 2025 üçüncü çeyrek net kârı ne kadar?"


async def test_happy_path_cites_and_emits_events() -> None:
    router = ScriptedRouter([tool_response(*FACTS_CALL), answer_response(**GOOD)])
    events = Events()
    outcome = await run_agent(
        QUESTION, router, FakeTools({"get_financial_facts": [FACT]}), Budget(), events
    )
    assert outcome.status == "completed"
    assert outcome.answer.disclaimer == DISCLAIMER
    assert outcome.citations == [{"cite_id": "f:1", "excerpt": FACT.text, "source": FACT.source}]
    assert events.names() == ["model_called", "tool_called", "tool_result", "model_called"]
    assert events.events[0][1] == {
        "task_class": "cheap",
        "provider": "groq",
        "cache_hit": False,
        "fallback_from": None,
    }
    assert events.events[1][1] == {
        "tool": "get_financial_facts",
        "summary": "summary of get_financial_facts",
    }
    assert outcome.usage["model_calls"] == 2
    assert outcome.usage["tool_calls"] == 1


async def test_last_call_is_forced_strong_answer() -> None:
    router = ScriptedRouter(
        [tool_response(*FACTS_CALL, call_id=str(i)) for i in range(3)] + [answer_response(**GOOD)]
    )
    outcome = await run_agent(
        QUESTION,
        router,
        FakeTools({"get_financial_facts": [FACT]}),
        Budget(max_model_calls=4),
        Events(),
    )
    assert [c for c, _ in router.calls] == ["cheap", "cheap", "cheap", "strong"]
    final = router.calls[-1][1]
    assert final.tools == [] and final.response_schema is not None
    assert outcome.status == "completed"
    assert len(router.calls) == 4  # never more than the limit


async def test_tool_call_limit_enforced() -> None:
    many = ChatResponse(
        content=None,
        tool_calls=[
            ToolCall(id=str(i), name=FACTS_CALL[0], arguments=FACTS_CALL[1]) for i in range(5)
        ],
        provider="groq",
        model="m",
    )
    tools = FakeTools({"get_financial_facts": [FACT]})
    router = ScriptedRouter([many, many, answer_response(**GOOD)])
    outcome = await run_agent(
        QUESTION, router, tools, Budget(max_model_calls=3, max_tool_calls=6), Events()
    )
    assert len(tools.calls) == 6
    assert outcome.usage["tool_calls"] == 6
    limit_msgs = [m for m in router.calls[-1][1].messages if m.content and "limit" in m.content]
    assert len(limit_msgs) == 4


async def test_deadline_stops_the_run() -> None:
    clock = [0.0]
    router = ScriptedRouter(
        [tool_response(*FACTS_CALL), tool_response(*FACTS_CALL), answer_response(**GOOD)], clock
    )
    outcome = await run_agent(
        QUESTION,
        router,
        FakeTools({"get_financial_facts": [FACT]}),
        Budget(deadline_seconds=90),
        Events(),
        clock=lambda: clock[0],
    )
    # calls at t=0 and t=40 run; at t=80 one more starts; at t=120 the deadline is passed
    assert len(router.calls) == 3
    assert outcome.status in ("completed", "partial")
    clock[0] = 0.0
    slow = ScriptedRouter([tool_response(*FACTS_CALL)] * 4, [0.0])
    slow.clock = clock
    outcome = await run_agent(
        QUESTION, slow, FakeTools(), Budget(deadline_seconds=50), Events(), clock=lambda: clock[0]
    )
    assert len(slow.calls) == 2
    assert outcome.status == "partial"
    assert outcome.usage["deadline_exceeded"] is True


async def test_reask_fixes_answer() -> None:
    bad = {**GOOD, "summary": "Net kâr 9.999 milyon TL [1]."}
    router = ScriptedRouter(
        [tool_response(*FACTS_CALL), answer_response(**bad), answer_response(**GOOD)]
    )
    outcome = await run_agent(
        QUESTION, router, FakeTools({"get_financial_facts": [FACT]}), Budget(), Events()
    )
    assert outcome.status == "completed"
    reask = router.calls[-1][1].messages[-1].content
    assert reask is not None and "9.999" in reask
    assert [c for c, _ in router.calls] == ["cheap", "cheap", "strong"]


async def test_second_failure_is_partial_with_valid_points() -> None:
    bad = {
        "summary": "Net kâr 9.999 milyon TL [1].",
        "points": [
            {"text": "Net kâr 1.234,5 milyon TL", "cite_ids": ["f:1"]},
            {"text": "Gelir 77 milyon TL", "cite_ids": ["f:1"]},
        ],
        "citations": [{"marker": 1, "cite_id": "f:1"}],
    }
    router = ScriptedRouter(
        [tool_response(*FACTS_CALL), answer_response(**bad), answer_response(**bad)]
    )
    outcome = await run_agent(
        QUESTION, router, FakeTools({"get_financial_facts": [FACT]}), Budget(), Events()
    )
    assert outcome.status == "partial"
    assert [p.text for p in outcome.answer.points] == ["Net kâr 1.234,5 milyon TL"]
    assert outcome.answer.summary == ""


async def test_no_reask_when_no_call_left() -> None:
    bad = {**GOOD, "summary": "Net kâr 9.999 milyon TL [1]."}
    router = ScriptedRouter([tool_response(*FACTS_CALL), answer_response(**bad)])
    outcome = await run_agent(
        QUESTION,
        router,
        FakeTools({"get_financial_facts": [FACT]}),
        Budget(max_model_calls=2),
        Events(),
    )
    assert len(router.calls) == 2
    assert outcome.status == "partial"


async def test_unparsable_answer_is_partial() -> None:
    junk = ChatResponse(content="not json", provider="gemini", model="m")
    router = ScriptedRouter([tool_response(*FACTS_CALL), junk, junk])
    outcome = await run_agent(QUESTION, router, FakeTools(), Budget(), Events())
    assert outcome.status == "partial"
    assert outcome.answer.points == []


async def test_advice_question_is_refused() -> None:
    info = {
        "status": "completed",
        "summary": "Net kâr 1.234,5 milyon TL [1].",
        "citations": [{"marker": 1, "cite_id": "f:1"}],
    }
    router = ScriptedRouter([tool_response(*FACTS_CALL), answer_response(**info)])
    outcome = await run_agent(
        "THYAO almalı mıyım?",
        router,
        FakeTools({"get_financial_facts": [FACT]}),
        Budget(),
        Events(),
    )
    assert outcome.status == "refused"
    assert outcome.answer.status == "refused"
    user = router.calls[0][1].messages[1].content
    assert user is not None and "refused" in user


async def test_company_not_in_corpus_is_no_evidence() -> None:
    note = {"status": "completed", "summary": "XYZ şirketi kapsamda değil."}
    router = ScriptedRouter(
        [tool_response("search_documents", {"query": "XYZ net kâr"}), answer_response(**note)]
    )
    outcome = await run_agent(
        "XYZ şirketinin net kârı nedir?", router, FakeTools(), Budget(), Events()
    )
    assert outcome.status == "no_evidence"
    assert outcome.answer.summary == "XYZ şirketi kapsamda değil."
    assert outcome.citations == []


async def test_injection_chunk_is_not_followed() -> None:
    obeyed = {
        "summary": "Hedef fiyat 500 TL [1].",
        "citations": [{"marker": 1, "cite_id": "f:999"}],
    }
    clean = {"summary": "Net kâr açıklandı [1].", "citations": [{"marker": 1, "cite_id": "c:p"}]}
    router = ScriptedRouter(
        [
            tool_response("search_documents", {"query": "net kâr"}),
            answer_response(**obeyed),
            answer_response(**clean),
        ]
    )
    outcome = await run_agent(
        QUESTION, router, FakeTools({"search_documents": [POISON]}), Budget(), Events()
    )
    tool_message = router.calls[1][1].messages[3].content
    assert tool_message is not None and tool_message.count("</untrusted_document>") == 1
    assert outcome.status == "completed"
    assert "500" not in outcome.answer.summary
    assert {c["cite_id"] for c in outcome.citations} == {"c:p"}


async def test_english_question_and_fake_router_replay() -> None:
    en = {
        "language": "en",
        "summary": "Net income was 1,234.5 million TRY [1].",
        "citations": [{"marker": 1, "cite_id": "f:1"}],
    }
    question = "What was THYAO net income in 2025 Q3?"
    scripted = ScriptedRouter([tool_response(*FACTS_CALL), answer_response(**en)])
    tools = FakeTools({"get_financial_facts": [FACT]})
    first = await run_agent(question, scripted, tools, Budget(), Events())
    replay = FakeRouter()
    for (task_class, request), response in zip(
        scripted.calls, [tool_response(*FACTS_CALL), answer_response(**en)], strict=True
    ):
        replay.add(request, task_class, response)
    second = await run_agent(question, replay, tools, Budget(), Events())
    assert first.answer == second.answer
    assert second.language == "en" and second.status == "completed"


@pytest.mark.parametrize("fence", ["```json\n{}\n```", "{}"])
def test_parse_answer_strips_fences(fence: str) -> None:
    from agent.loop import parse_answer

    body = json.dumps({"language": "tr", "status": "completed", "summary": "x"})
    answer, errors = parse_answer(fence.replace("{}", body))
    assert answer is not None and errors == []
