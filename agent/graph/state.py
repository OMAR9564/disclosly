"""Graph state and its value objects (data-model.md "Graph state", PROJECT_SPEC §6.1).

The state is checkpointed after every node, so every field is a Pydantic model or a plain
value the LangGraph serializer can store. `STATE_TYPES` is the serializer's allowlist: a
checkpoint can only rebuild these classes (agent/checkpoint.py).
"""

import operator
import re
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.answer import CitationRef, Language, Point
from agent.budget import LimitHit, Mode, ModeBudget, NodeUsage, Usage
from agent.plan import Estimate, Plan, PlanStep
from agent.report import Cell, Chart, Confidence, Report, Table
from tools.types import SeenItem

PeriodRange = Literal["last_4q", "last_8q", "last_2fy", "last_3fy"]
CODE_MAX_CHARS = 4000
CALC_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
CALC_CITE_ID = re.compile(r"^k:[1-9][0-9]*$")


class MemoryIntent(BaseModel):
    """A watchlist change the user's own question asks for (never from retrieved text)."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["watchlist_add", "watchlist_remove"]
    ticker: str = Field(min_length=1, max_length=12)

    @field_validator("ticker")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()


class RouterDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode
    tickers: list[str] = Field(default_factory=list, max_length=8)
    periods: list[str] = Field(default_factory=list, max_length=12)
    metrics: list[str] = Field(default_factory=list, max_length=10)
    uses_watchlist: bool = False
    memory_intent: MemoryIntent | None = None

    @field_validator("tickers")
    @classmethod
    def _upper(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(t.strip().upper() for t in value if t.strip()))


class MemorySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    watchlist: list[str] = Field(default_factory=list)
    answer_language: Language | None = None
    default_period_range: PeriodRange | None = None


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1, max_length=1000)
    cite_ids: list[str] = Field(min_length=1)
    calcs: list[str] = Field(default_factory=list)


class CalculationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=CALC_NAME.pattern)
    cite_id: str
    value: Decimal


class Calculation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cite_id: str = Field(pattern=CALC_CITE_ID.pattern)
    name: str = Field(pattern=CALC_NAME.pattern)
    description: str = Field(default="", max_length=500)
    inputs: list[CalculationInput] = Field(default_factory=list)
    code: str = Field(max_length=CODE_MAX_CHARS)
    output: Decimal | None = None
    error: str | None = None
    verified: bool = False
    # Set by the verifier once it has looked at the calculation (passed or not).
    checked: bool = False


class VerificationFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["input_mismatch", "recompute_mismatch", "unsupported_claim", "sandbox_error"]
    ref: str
    reason: str


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round: int = Field(ge=1)
    passed: bool
    failures: list[VerificationFailure] = Field(default_factory=list)


def merge_calculations(
    current: list[Calculation] | None, new: list[Calculation] | None
) -> list[Calculation]:
    """Reducer: a calculation with a known `cite_id` replaces the old one; others append."""
    merged = list(current or [])
    index = {calc.cite_id: i for i, calc in enumerate(merged)}
    for calc in new or []:
        if calc.cite_id in index:
            merged[index[calc.cite_id]] = calc
        else:
            index[calc.cite_id] = len(merged)
            merged.append(calc)
    return merged


def add_evidence(current: list[SeenItem] | None, new: list[SeenItem] | None) -> list[SeenItem]:
    """Reducer: append new items, keeping the first item of each `cite_id`."""
    merged = list(current or [])
    known = {item.cite_id for item in merged}
    for item in new or []:
        if item.cite_id not in known:
            known.add(item.cite_id)
            merged.append(item)
    return merged


class ResearchState(TypedDict, total=False):
    run_id: str
    user_id: str
    question: str
    # When the question was accepted; a simple run's deadline counts from here, so time spent
    # waiting for a free run slot counts too.
    submitted_at: datetime
    language: Language
    mode: Mode | None
    # The router found an investment-advice question: no plan, a refused report.
    refused: bool
    router: RouterDecision | None
    memory: MemorySnapshot
    # What the router took from memory (FR-030): the report says so.
    used_watchlist: bool
    # Watchlist companies beyond what one plan can cover (named in the plan and report).
    watchlist_left_out: list[str]
    # The question named no period, so the user's default range was used.
    used_period_range: PeriodRange | None
    # The outcome of `memory_intent`: "added", "removed", "unchanged" or an error code.
    memory_result: str | None
    plan: Plan | None
    plan_approved: bool
    # The pending decision's kind while the graph waits at `decision`.
    decision_kind: Literal["plan", "deep_quota_offer"] | None
    cancelled: bool
    evidence: Annotated[list[SeenItem], add_evidence]
    findings: list[Finding]
    calculations: Annotated[list[Calculation], merge_calculations]
    verification: VerificationResult | None
    verify_rounds: int
    # What the last verification round dropped; the writer reports it and marks the run partial.
    unverified: list[str]
    draft: Report | None
    budget: ModeBudget
    deadline_at: datetime | None
    usage: Usage
    limit_hit: LimitHit | None
    errors: Annotated[list[str], operator.add]


def evidence_by_id(items: Iterable[SeenItem]) -> dict[str, SeenItem]:
    return {item.cite_id: item for item in items}


# Classes a checkpoint may rebuild (LangGraph msgpack allowlist).
STATE_TYPES: tuple[type[BaseModel], ...] = (
    MemoryIntent,
    RouterDecision,
    MemorySnapshot,
    Finding,
    CalculationInput,
    Calculation,
    VerificationFailure,
    VerificationResult,
    SeenItem,
    Plan,
    PlanStep,
    Estimate,
    Report,
    Table,
    Cell,
    Chart,
    Confidence,
    Point,
    CitationRef,
    ModeBudget,
    Usage,
    NodeUsage,
)
