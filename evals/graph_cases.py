"""Graph eval datasets and cassettes (specs/004-multi-agent-graph, research §R16).

Datasets:
- `deep_golden.jsonl`: `question`, `language`, `expected_mode`, `expected_status`,
  `expected_numbers[{value, tolerance}]` (in the unit the question's figures are usually
  stated in, e.g. billions), `expected_tables`, `expected_chart`;
- the graph-only cases of `advice_refusal.jsonl` and `injection.jsonl` (`engines` includes
  `graph`), loaded with the agent case models.

A cassette `evals/cassettes/graph/<case_id>.json` holds one recorded graph run: the model
interactions keyed as `llm.testing` reads them, the tool results keyed by tool name and
arguments, and the sandbox outputs. No request headers are stored, so no auth header can leak.
The recorder approves the proposed plan, except for plan-edit cases, which stop at the plan.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field

from agent.budget import Usage
from agent.checkpoint import graph_serde
from agent.graph.build import DURABILITY, build_graph
from agent.graph.context import GraphDeps
from agent.graph.nodes.decision import Decision
from agent.graph.state import ResearchState
from agent.plan import Plan, Reason, validate_edit
from agent.report import Report
from evals.agent_cases import (
    DATASETS_DIR,
    EVALS_DIR,
    AdviceCase,
    ExpectedNumber,
    InjectionCase,
    RecordedTool,
    ToolCassetteMiss,
    _Case,
    load_dataset,
    poisoned_item,
    tool_key,
)
from llm.router import Router
from llm.testing import FakeRouter
from llm.types import ToolCall, ToolSchema
from tools.registry import NODE_TOOLS, SCHEMAS, GraphNode, ToolNotAllowed, ToolResult, wrap
from tools.types import SeenItem

CASSETTES_DIR = EVALS_DIR / "cassettes" / "graph"

GraphDataset = Literal["deep_golden", "advice_refusal", "injection"]
GRAPH_DATASETS: tuple[GraphDataset, ...] = ("deep_golden", "advice_refusal", "injection")


class DeepCase(_Case):
    expected_mode: Literal["simple", "deep"] = "deep"
    expected_status: Literal["completed", "partial", "no_evidence"] = "completed"
    expected_numbers: list[ExpectedNumber] = Field(default_factory=list)
    expected_tables: int = Field(default=0, ge=0, le=4)
    expected_chart: bool = False


GraphCase = DeepCase | AdviceCase | InjectionCase


def load_graph_dataset(name: GraphDataset, directory: Path = DATASETS_DIR) -> list[Any]:
    """The dataset's cases that apply to the graph engine."""
    if name == "deep_golden":
        lines = (directory / "deep_golden.jsonl").read_text(encoding="utf-8").splitlines()
        cases: list[Any] = [DeepCase.model_validate_json(line) for line in lines if line.strip()]
        ids = [c.id for c in cases]
        if len(ids) != len(set(ids)):
            raise ValueError("deep_golden: duplicate case ids")
    else:
        cases = load_dataset(name, directory)
    return [case for case in cases if "graph" in case.engines]


class SandboxRecord(BaseModel):
    """One recorded sandbox execution (calculations, T035): request key and response."""

    key: str
    response: dict[str, Any]


class GraphCassette(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    dataset: GraphDataset
    interactions: list[dict[str, Any]]
    tools: list[RecordedTool]
    sandbox: list[SandboxRecord] = Field(default_factory=list)
    # The decision the recorder sent at the plan pause, or None when the run never paused.
    decision: Literal["approve", "edit"] | None = None


def cassette_path(case_id: str, directory: Path = CASSETTES_DIR) -> Path:
    return directory / f"{case_id}.json"


def read_cassette(path: Path) -> GraphCassette:
    return GraphCassette.model_validate_json(path.read_text(encoding="utf-8"))


def write_cassette(cassette: GraphCassette, directory: Path = CASSETTES_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = cassette_path(cassette.case_id, directory)
    path.write_text(
        json.dumps(cassette.model_dump(mode="json"), ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    return path


def recorded(call: ToolCall, result: ToolResult) -> RecordedTool:
    return RecordedTool(
        key=tool_key(call.name, call.arguments),
        tool=result.tool,
        content=result.content,
        summary=result.summary,
        count=result.count,
        items=result.items,
        error=result.error,
    )


def with_poison(result: ToolResult, case: InjectionCase) -> ToolResult:
    item: SeenItem = poisoned_item(case)
    items = [*result.items, item]
    return ToolResult(
        result.tool, "\n".join(wrap(i) for i in items), result.summary, items, len(items)
    )


class _ReplayView:
    def __init__(self, node: GraphNode, queue: dict[str, list[RecordedTool]]) -> None:
        self.node = node
        self.names: frozenset[str] = frozenset(NODE_TOOLS[node])
        self._queue = queue

    @property
    def schemas(self) -> list[ToolSchema]:
        return [schema for schema in SCHEMAS if schema.name in self.names]

    @property
    def tool_schema_version(self) -> str:
        return "2"

    async def dispatch(self, call: ToolCall, language: str = "tr") -> ToolResult:
        if call.name not in self.names:
            raise ToolNotAllowed(self.node, call.name)
        queue = self._queue.get(tool_key(call.name, call.arguments))
        if not queue:
            raise ToolCassetteMiss(f"no recorded result for {call.name} {call.arguments}")
        entry = queue.pop(0)
        return ToolResult(
            entry.tool, entry.content, entry.summary, entry.items, entry.count, entry.error
        )


class ReplayToolbox:
    """The recorded tool results, in order, for identical calls; per-node tool sets apply."""

    def __init__(self, entries: list[RecordedTool]) -> None:
        self._queue: dict[str, list[RecordedTool]] = {}
        for entry in entries:
            self._queue.setdefault(entry.key, []).append(entry)

    def for_node(self, node: GraphNode) -> Any:
        return _ReplayView(node, self._queue)


def replay(cassette: GraphCassette) -> tuple[FakeRouter, ReplayToolbox]:
    router = FakeRouter()
    router.load_interactions(cassette.interactions)
    return router, ReplayToolbox(cassette.tools)


@dataclass
class GraphOutcome:
    """A finished (or, for plan-edit cases, paused) graph run."""

    values: dict[str, Any]
    paused_plan: Plan | None = None
    edit_reasons: list[Reason] = field(default_factory=list)

    @property
    def report(self) -> Report | None:
        draft = self.values.get("draft")
        return draft if isinstance(draft, Report) else None


async def run_graph_case(case: Any, router: Router, toolbox: Any) -> GraphOutcome:
    """Run one case through the graph; approve the plan, or try the case's plan edit."""
    deps = GraphDeps(router=router, tools=toolbox)
    graph = build_graph(deps, InMemorySaver(serde=graph_serde()))
    config: RunnableConfig = {"configurable": {"thread_id": case.id}}
    initial: ResearchState = {
        "run_id": case.id,
        "user_id": "eval",
        "question": case.question,
        "language": case.language,
        "usage": Usage(),
        "evidence": [],
        "calculations": [],
        "errors": [],
        "verify_rounds": 0,
        "plan_approved": False,
        "cancelled": False,
    }
    await graph.ainvoke(initial, config, durability=DURABILITY)
    snapshot = await graph.aget_state(config)
    if snapshot.next == ("decision",):
        values = dict(snapshot.values)
        plan = values.get("plan")
        edit_goal = getattr(case, "plan_edit_goal", None)
        if edit_goal and isinstance(plan, Plan):
            step = plan.steps[0].model_copy(update={"goal": edit_goal})
            edited = plan.model_copy(update={"steps": [step, *plan.steps[1:]]})
            usage = values.get("usage") or Usage()
            reasons = validate_edit(edited, deps.budgets["deep"], usage.model_calls)
            return GraphOutcome(values, plan, reasons)
        decision = Decision(action="approve", decided_at=datetime.now(UTC))
        await graph.ainvoke(
            Command(resume=decision.model_dump(mode="json")), config, durability=DURABILITY
        )
        snapshot = await graph.aget_state(config)
    return GraphOutcome(dict(snapshot.values))
