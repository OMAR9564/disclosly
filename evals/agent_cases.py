"""Agent eval datasets and cassettes (contracts/agent-answer.md, "Eval cassettes").

Datasets (JSONL, one case per line):
- `answers_golden.jsonl`: `question`, `language`, `expected_status`,
  `expected_numbers[{value, tolerance}]`, `expected_cite_ids[]`;
- `advice_refusal.jsonl`: `question`, `language`; Phase 4 deep cases may add
  `plan_edit_goal` (advice hidden in an edited plan step);
- `injection.jsonl`: `question`, `language`, `poisoned_chunk`, `forbidden_behaviour`,
  `forbidden_strings[]` (checked automatically).

`engines` names the engines a case applies to (default both); the single-agent harness skips
cases without `single`, such as plan edits, which only the graph has.

A cassette `evals/cassettes/agent/<case_id>.json` holds the model interactions (request key,
task class, response, as `llm.testing` reads them) and the tool results of one recorded run.
No request headers are stored, so no auth header can leak into a cassette.
"""

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from llm.cache_key import canonical_json
from llm.testing import FakeRouter
from llm.types import ToolCall, ToolSchema
from tools.registry import SCHEMAS, ToolResult
from tools.types import SeenItem

EVALS_DIR = Path(__file__).parent
DATASETS_DIR = EVALS_DIR / "datasets"
CASSETTES_DIR = EVALS_DIR / "cassettes" / "agent"

Dataset = Literal["answers_golden", "advice_refusal", "injection"]
DATASETS: tuple[Dataset, ...] = ("answers_golden", "advice_refusal", "injection")
Language = Literal["tr", "en"]
Engine = Literal["single", "graph"]
ENGINES: tuple[Engine, ...] = ("single", "graph")


class _Case(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    question: str = Field(min_length=3, max_length=1000)
    language: Language
    engines: list[Engine] = Field(default_factory=lambda: list(ENGINES), min_length=1)


class ExpectedNumber(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float
    tolerance: float = Field(default=0.0, ge=0)
    # A derived number (ratio, growth, margin): it must come from a verified calculation.
    derived: bool = False


class GoldenCase(_Case):
    expected_status: Literal["completed", "no_evidence"] = "completed"
    expected_numbers: list[ExpectedNumber] = Field(default_factory=list)
    expected_cite_ids: list[str] = Field(default_factory=list)


class AdviceCase(_Case):
    # Advice placed in a step goal of an edited plan; the edit must be rejected.
    plan_edit_goal: str | None = Field(default=None, min_length=1, max_length=300)


class MemoryFixture(BaseModel):
    """The user's saved memory for one graph case (the single-agent harness ignores it).

    `latest_period` stands in for the latest period with facts, which the router looks up
    when it applies the default range."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    watchlist: list[str] = Field(default_factory=list, max_length=50)
    default_period_range: Literal["last_4q", "last_8q", "last_2fy", "last_3fy"] | None = None
    latest_period: str | None = Field(default=None, pattern=r"^\d{4}-(Q[1-4]|FY)$")


class InjectionCase(_Case):
    poisoned_chunk: str = Field(min_length=1)
    forbidden_behaviour: str = Field(min_length=1)
    forbidden_strings: list[str] = Field(min_length=1)
    # Graph only: the memory the poisoned run starts with; it must stay unchanged.
    memory: MemoryFixture | None = None


AgentCase = GoldenCase | AdviceCase | InjectionCase
_MODELS: dict[Dataset, type[_Case]] = {
    "answers_golden": GoldenCase,
    "advice_refusal": AdviceCase,
    "injection": InjectionCase,
}


def load_dataset(name: Dataset, directory: Path = DATASETS_DIR) -> list[Any]:
    model = _MODELS[name]
    lines = (directory / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
    cases = [model.model_validate_json(line) for line in lines if line.strip()]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{name}: duplicate case ids")
    return cases


def poisoned_item(case: InjectionCase) -> SeenItem:
    return SeenItem(
        cite_id=f"c:poisoned-{case.id}",
        text=case.poisoned_chunk,
        source={"kind": "chunk", "poisoned": True},
    )


def tool_key(name: str, arguments: dict[str, Any]) -> str:
    return canonical_json({"name": name, "arguments": arguments})


class RecordedTool(BaseModel):
    key: str
    tool: str
    content: str
    summary: str
    count: int
    items: list[SeenItem]
    error: str | None = None


class Cassette(BaseModel):
    case_id: str
    dataset: Dataset
    interactions: list[dict[str, Any]]
    tools: list[RecordedTool]


def cassette_path(case_id: str, directory: Path = CASSETTES_DIR) -> Path:
    return directory / f"{case_id}.json"


def read_cassette(path: Path) -> Cassette:
    return Cassette.model_validate_json(path.read_text(encoding="utf-8"))


def write_cassette(cassette: Cassette, directory: Path = CASSETTES_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = cassette_path(cassette.case_id, directory)
    path.write_text(
        json.dumps(cassette.model_dump(mode="json"), ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    return path


class ToolCassetteMiss(LookupError):
    pass


class ReplayTools:
    """Returns the recorded tool results, in order, for identical calls."""

    def __init__(self, recorded: list[RecordedTool]) -> None:
        self._queue: dict[str, list[RecordedTool]] = {}
        for entry in recorded:
            self._queue.setdefault(entry.key, []).append(entry)

    @property
    def schemas(self) -> list[ToolSchema]:
        return SCHEMAS

    async def dispatch(self, call: ToolCall, language: str = "tr") -> ToolResult:
        queue = self._queue.get(tool_key(call.name, call.arguments))
        if not queue:
            raise ToolCassetteMiss(f"no recorded result for {call.name} {call.arguments}")
        entry = queue.pop(0)
        return ToolResult(
            entry.tool, entry.content, entry.summary, entry.items, entry.count, entry.error
        )


def replay(cassette: Cassette) -> tuple[FakeRouter, ReplayTools]:
    router = FakeRouter()
    router.load_interactions(cassette.interactions)
    return router, ReplayTools(cassette.tools)
