"""`FakeRouter`: replays recorded model responses so tests never call a live provider.

A cassette is a JSON file `{"interactions": [{"key", "task_class", "response"}]}`. The key is
`cassette_key(request, task_class)`, i.e. `llm.cache_key.cache_key` with the task class in
place of the model id (the recorded provider is in `response`). An unknown key raises
`CassetteMiss` so a test can never fall through to a live call.
"""

import json
from pathlib import Path
from typing import Any

from llm.cache_key import cache_key
from llm.types import ChatRequest, ChatResponse, TaskClass


class CassetteMiss(LookupError):
    """No recorded response for this request."""


def cassette_key(request: ChatRequest, task_class: TaskClass) -> str:
    return cache_key(request, model=f"task-class:{task_class}")


class FakeRouter:
    def __init__(self) -> None:
        self._responses: dict[str, ChatResponse] = {}
        self.calls: list[tuple[TaskClass, ChatRequest]] = []

    def add(self, request: ChatRequest, task_class: TaskClass, response: ChatResponse) -> str:
        key = cassette_key(request, task_class)
        self._responses[key] = response
        return key

    def load(self, path: str | Path) -> "FakeRouter":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for interaction in data["interactions"]:
            self._responses[interaction["key"]] = ChatResponse.model_validate(
                interaction["response"]
            )
        return self

    @classmethod
    def from_cassette(cls, path: str | Path) -> "FakeRouter":
        return cls().load(path)

    async def chat(self, request: ChatRequest, *, task_class: TaskClass) -> ChatResponse:
        self.calls.append((task_class, request))
        key = cassette_key(request, task_class)
        try:
            return self._responses[key].model_copy()
        except KeyError:
            raise CassetteMiss(f"no cassette entry for {task_class} request {key}") from None


def interaction(
    request: ChatRequest, task_class: TaskClass, response: ChatResponse
) -> dict[str, Any]:
    """One cassette entry, as the recorder writes it."""
    return {
        "key": cassette_key(request, task_class),
        "task_class": task_class,
        "response": response.model_dump(mode="json"),
    }
