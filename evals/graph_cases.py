"""Graph eval datasets and cassettes (specs/004-multi-agent-graph, research §R16).

Datasets:
- `deep_golden.jsonl`: `question`, `language`, `expected_mode`, `expected_status`,
  `expected_numbers[{value, tolerance}]` (in the unit the question's figures are usually
  stated in, e.g. billions), `expected_tables`, `expected_chart`;
- the graph-only cases of `advice_refusal.jsonl` and `injection.jsonl` (`engines` includes
  `graph`), loaded with the agent case models;
- `verifier_seeded.jsonl`: drafts (evidence, findings, calculations) with a seeded defect, or
  none, that only the verifier node sees (T037).

A cassette `evals/cassettes/graph/<case_id>.json` holds one recorded graph run: the model
interactions keyed as `llm.testing` reads them, the tool results keyed by tool name and
arguments, and the sandbox outputs. No request headers are stored, so no auth header can leak.
The recorder approves the proposed plan, except for plan-edit cases, which stop at the plan.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field

from agent.budget import Usage
from agent.checkpoint import graph_serde
from agent.graph.build import DURABILITY, build_graph
from agent.graph.context import GraphDeps, NodeContext
from agent.graph.nodes.decision import Decision
from agent.graph.nodes.verifier import verifier_node
from agent.graph.state import Calculation, Finding, ResearchState, VerificationResult
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
from tools.python import Sandbox, SandboxRun
from tools.registry import NODE_TOOLS, SCHEMAS, GraphNode, ToolNotAllowed, ToolResult, wrap
from tools.types import SeenItem

CASSETTES_DIR = EVALS_DIR / "cassettes" / "graph"

GraphDataset = Literal["deep_golden", "advice_refusal", "injection", "verifier_seeded"]
GRAPH_DATASETS: tuple[GraphDataset, ...] = ("deep_golden", "advice_refusal", "injection")
SEEDED: GraphDataset = "verifier_seeded"


class DeepCase(_Case):
    expected_mode: Literal["simple", "deep"] = "deep"
    expected_status: Literal["completed", "partial", "no_evidence"] = "completed"
    expected_numbers: list[ExpectedNumber] = Field(default_factory=list)
    expected_tables: int = Field(default=0, ge=0, le=4)
    expected_chart: bool = False


SeededDefect = Literal[
    "wrong_output", "wrong_fact_input", "chunk_number", "uncited_number", "unsupported_claim"
]
FailureKind = Literal["input_mismatch", "recompute_mismatch", "unsupported_claim", "sandbox_error"]


class SeededCase(BaseModel):
    """A draft for the verifier alone: the evidence, the analyst's findings and calculations
    (with their recorded outputs), and the defect seeded in it, if any."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    question: str = Field(min_length=3, max_length=1000)
    language: Literal["tr", "en"]
    evidence: list[SeenItem] = Field(min_length=1)
    findings: list[Finding] = Field(min_length=1)
    calculations: list[Calculation] = Field(default_factory=list)
    defect: SeededDefect | None = None
    expected_rejected: bool
    expected_failure: FailureKind | None = None
    engines: list[str] = Field(default_factory=lambda: ["graph"])


GraphCase = DeepCase | AdviceCase | InjectionCase | SeededCase


def _read_cases(name: str, model: type[BaseModel], directory: Path) -> list[Any]:
    lines = (directory / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
    cases: list[Any] = [model.model_validate_json(line) for line in lines if line.strip()]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{name}: duplicate case ids")
    return cases


def load_graph_dataset(name: GraphDataset, directory: Path = DATASETS_DIR) -> list[Any]:
    """The dataset's cases that apply to the graph engine."""
    if name == "deep_golden":
        cases = _read_cases(name, DeepCase, directory)
    elif name == "verifier_seeded":
        cases = _read_cases(name, SeededCase, directory)
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


class SandboxCassetteMiss(Exception):
    """A calculation batch that was not recorded: the cassette is stale."""


def sandbox_key(code: str, inputs: Mapping[str, Decimal]) -> str:
    body = json.dumps(
        {"code": code, "inputs": {k: str(v) for k, v in sorted(inputs.items())}},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _run_record(run: SandboxRun) -> dict[str, Any]:
    return {
        "ok": run.ok,
        "outputs": {k: str(v) for k, v in run.outputs.items()},
        "stdout": run.stdout,
        "error": run.error,
        "timed_out": run.timed_out,
        "oom": run.oom,
    }


class RecordingSandbox:
    """Runs batches on a real sandbox and keeps each response for the cassette."""

    def __init__(self, inner: Sandbox) -> None:
        self._inner = inner
        self.records: list[SandboxRecord] = []

    async def run(
        self, code: str, inputs: Mapping[str, Decimal], *, timeout_s: float
    ) -> SandboxRun:
        result = await self._inner.run(code, inputs, timeout_s=timeout_s)
        self.records.append(
            SandboxRecord(key=sandbox_key(code, inputs), response=_run_record(result))
        )
        return result


class ReplaySandbox:
    """Recorded sandbox responses, in order, for identical batches."""

    def __init__(self, records: list[SandboxRecord]) -> None:
        self._queue: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            self._queue.setdefault(record.key, []).append(record.response)

    async def run(
        self, code: str, inputs: Mapping[str, Decimal], *, timeout_s: float
    ) -> SandboxRun:
        queue = self._queue.get(sandbox_key(code, inputs))
        if not queue:
            raise SandboxCassetteMiss("no recorded sandbox run for this calculation batch")
        data = queue.pop(0)
        return SandboxRun(
            ok=bool(data["ok"]),
            outputs={k: Decimal(v) for k, v in data["outputs"].items()},
            stdout=data.get("stdout") or "",
            error=data.get("error"),
            timed_out=bool(data.get("timed_out")),
            oom=bool(data.get("oom")),
        )


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


def replay_sandbox(cassette: GraphCassette) -> ReplaySandbox:
    return ReplaySandbox(cassette.sandbox)


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


async def run_graph_case(
    case: Any, router: Router, toolbox: Any, sandbox: Sandbox | None = None
) -> GraphOutcome:
    """Run one case through the graph; approve the plan, or try the case's plan edit."""
    deps = GraphDeps(router=router, tools=toolbox, sandbox=sandbox)
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


async def run_seeded_case(
    case: SeededCase, router: Router, sandbox: Sandbox | None
) -> VerificationResult:
    """The verifier alone on a seeded draft (no database: inputs are checked against the
    draft's own evidence, which is where the seeded fact values live)."""
    deps = GraphDeps(router=router, tools=ReplayToolbox([]), sandbox=sandbox)
    deep = deps.budgets["deep"]
    ctx = NodeContext(
        node="verifier",
        attempt=1,
        deps=deps,
        run_id=case.id,
        usage=Usage(),
        budget=deep,
        deadline_at=None,
    )
    state: ResearchState = {
        "run_id": case.id,
        "question": case.question,
        "language": case.language,
        "evidence": list(case.evidence),
        "findings": list(case.findings),
        "calculations": list(case.calculations),
        "verify_rounds": 0,
    }
    update = await verifier_node(state, ctx)
    result: VerificationResult = update["verification"]
    return result
