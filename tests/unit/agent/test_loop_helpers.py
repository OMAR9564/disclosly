"""agent.loop helpers (T011): `research_turns` and `answer_turn` on their own."""

from typing import Any

import pytest

from agent.loop import (
    AnswerResult,
    DeadlineExceeded,
    Turns,
    answer_turn,
    research_turns,
)
from llm.types import ChatMessage, ChatRequest, ChatResponse, TaskClass, ToolCall
from tests.agent_fakes import (
    FACT,
    FACTS_CALL,
    GOOD,
    ScriptedRouter,
    answer_response,
    tool_response,
)
from tools.registry import SCHEMAS


class Harness:
    def __init__(self, router: ScriptedRouter, max_calls: int = 4, language: Any = "tr") -> None:
        self.router = router
        self.max_calls = max_calls
        self.used = 0
        self.tool_calls: list[ToolCall] = []
        self.messages = [ChatMessage(role="user", content="THYAO net kâr?")]
        self.turns = Turns(self.call, self.calls_left, self.run_tools, self.messages, language)

    async def call(self, task_class: TaskClass, request: ChatRequest) -> ChatResponse:
        self.used += 1
        return await self.router.chat(request, task_class=task_class)

    def calls_left(self) -> int:
        return self.max_calls - self.used

    async def run_tools(self, calls: list[ToolCall]) -> None:
        self.tool_calls.extend(calls)
        for call in calls:
            self.messages.append(ChatMessage(role="tool", content="r", tool_call_id=call.id))


async def test_research_turns_keep_the_reserve() -> None:
    router = ScriptedRouter([tool_response(*FACTS_CALL, call_id=f"t{i}") for i in range(3)])
    harness = Harness(router, max_calls=4)

    candidate = await research_turns(harness.turns, SCHEMAS)

    assert candidate is None
    assert harness.used == 3  # one call kept for the answer
    assert len(harness.tool_calls) == 3
    assert all(task == "cheap" for task, _ in router.calls)
    assert router.calls[0][1].tool_schema_version == "1"


async def test_research_turns_return_a_candidate() -> None:
    router = ScriptedRouter([answer_response(**GOOD)])
    harness = Harness(router)

    candidate = await research_turns(harness.turns, SCHEMAS)

    assert candidate is not None
    assert "1.234,5" in candidate
    assert harness.used == 1


async def test_research_turns_max_turns_and_schema_version() -> None:
    router = ScriptedRouter([tool_response(*FACTS_CALL, call_id=f"t{i}") for i in range(3)])
    harness = Harness(router, max_calls=10)

    await research_turns(harness.turns, SCHEMAS[:1], max_turns=2, tool_schema_version="2")

    assert harness.used == 2
    assert [r.tool_schema_version for _, r in router.calls] == ["2", "2"]
    assert [t.name for t in router.calls[0][1].tools] == ["search_documents"]


async def test_answer_turn_validates_against_seen() -> None:
    harness = Harness(ScriptedRouter([answer_response(**GOOD)]))

    result = await answer_turn(harness.turns, {"f:1": FACT})

    assert result.answer is not None
    assert result.last is None
    assert harness.router.calls[0][0] == "strong"
    assert harness.router.calls[0][1].response_schema is not None


async def test_answer_turn_uses_the_candidate_without_a_call() -> None:
    harness = Harness(ScriptedRouter([]))
    candidate = answer_response(**GOOD).content

    result = await answer_turn(harness.turns, {"f:1": FACT}, candidate)

    assert result.answer is not None
    assert harness.used == 0


async def test_answer_turn_reasks_once_then_gives_up() -> None:
    bad = {**GOOD, "points": [{"text": "Net kâr 9.999 milyon TL", "cite_ids": ["f:1"]}]}
    router = ScriptedRouter([answer_response(**bad), answer_response(**bad)])
    harness = Harness(router)

    result = await answer_turn(harness.turns, {"f:1": FACT})

    assert result.answer is None
    assert result.last is not None
    assert harness.used == 2
    reask = router.calls[1][1].messages[-1]
    assert reask.role == "user"
    assert "9.999" in (reask.content or "")


async def test_answer_turn_without_calls_left() -> None:
    harness = Harness(ScriptedRouter([]), max_calls=0)
    result = await answer_turn(harness.turns, {"f:1": FACT})
    assert result == AnswerResult()


async def test_answer_turn_keeps_the_failed_answer_when_the_deadline_hits() -> None:
    bad = {**GOOD, "points": [{"text": "Net kâr 9.999 milyon TL", "cite_ids": ["f:1"]}]}
    harness = Harness(ScriptedRouter([answer_response(**bad)]))
    original = harness.call

    async def call(task_class: TaskClass, request: ChatRequest) -> ChatResponse:
        if harness.used >= 1:
            raise DeadlineExceeded
        return await original(task_class, request)

    harness.turns.call = call
    result = AnswerResult()
    with pytest.raises(DeadlineExceeded):
        await answer_turn(harness.turns, {"f:1": FACT}, None, result)
    assert result.last is not None
    assert result.answer is None
