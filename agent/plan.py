"""Research plans: structure, estimate and edit validation (research §R4, data-model.md).

The planner proposes a `Plan`; the owner approves, edits or cancels it. Edits come back as a
full structured plan and are checked here in code, with no model call: field limits, step
count, the advice detector on every goal and the call estimate against the calls left.
Unknown tickers are accepted; the retriever reports their steps as not covered.
"""

import re
from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from agent.advice import is_advice_request
from agent.budget import ModeBudget
from tools.facts import KNOWN_METRICS

DEFAULT_MAX_STEPS = 8
# Schema bound; the configured AGENT_PLAN_MAX_STEPS (default 8) is checked in validate_edit.
SCHEMA_MAX_STEPS = 16
MAX_TICKERS = 4
MAX_PERIODS = 8
GOAL_MAX_CHARS = 300

# Model calls per stage (research §R2): the estimate is computed here, never by the model.
ROUTER_CALLS = 1
PLANNER_CALLS = 1
RETRIEVER_CALLS = 1
ANALYST_CALLS = 1
VERIFIER_CALLS = 1
WRITER_CALLS = 1

StepKind = Literal["facts", "search", "calculate"]
StepStatus = Literal["pending", "done", "not_covered", "failed"]

_STEP_ID = re.compile(r"^s[1-9][0-9]?$")
_PERIOD = re.compile(r"^\d{4}-(Q[1-4]|FY)$")
_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=_STEP_ID.pattern)
    kind: StepKind
    goal: str = Field(min_length=1, max_length=GOAL_MAX_CHARS)
    tickers: list[str] = Field(default_factory=list, max_length=MAX_TICKERS)
    periods: list[str] = Field(default_factory=list, max_length=MAX_PERIODS)
    metrics: list[str] = Field(default_factory=list)
    status: StepStatus = "pending"

    @field_validator("goal")
    @classmethod
    def _strip_goal(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("goal must not be empty")
        return value

    @field_validator("tickers")
    @classmethod
    def _upper_tickers(cls, value: list[str]) -> list[str]:
        tickers = [t.strip().upper() for t in value]
        for ticker in tickers:
            if not _TICKER.match(ticker):
                raise ValueError("tickers must be upper-case symbols like THYAO or AAPL")
        return list(dict.fromkeys(tickers))

    @field_validator("periods")
    @classmethod
    def _check_periods(cls, value: list[str]) -> list[str]:
        for period in value:
            if not _PERIOD.match(period):
                raise ValueError("periods must look like 2025-Q3 or 2025-FY")
        return list(dict.fromkeys(value))

    @field_validator("metrics")
    @classmethod
    def _known_metrics(cls, value: list[str]) -> list[str]:
        if unknown := [m for m in value if m not in KNOWN_METRICS]:
            raise ValueError(f"unknown metrics {unknown}; allowed: {list(KNOWN_METRICS)}")
        return list(dict.fromkeys(value))


class Estimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    typical: int = Field(ge=0)
    max: int = Field(ge=0)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[PlanStep] = Field(min_length=1, max_length=SCHEMA_MAX_STEPS)
    estimate: Estimate | None = None
    edited: bool = False
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [step.id for step in self.steps]
        if len(set(ids)) != len(ids):
            raise ValueError("step ids must be unique")
        return self

    def tickers(self) -> set[str]:
        return {t for step in self.steps for t in step.tickers}

    def periods(self) -> set[str]:
        return {p for step in self.steps for p in step.periods}

    def kinds(self) -> set[StepKind]:
        return {step.kind for step in self.steps}


class Reason(BaseModel):
    """One reason an edited plan was rejected (`plan_invalid` `details.reasons`)."""

    step: str | None
    field: str
    reason: str


def plan_json_schema() -> dict[str, Any]:
    """What the planner model fills in: steps only; estimate and statuses are set in code."""
    schema = Plan.model_json_schema()
    for name in ("estimate", "edited", "revision"):
        schema["properties"].pop(name, None)
    return schema


def renumber(plan: Plan) -> Plan:
    """Step ids `s1`… in order and every status `pending` (a decision starts the steps anew)."""
    steps = [
        step.model_copy(update={"id": f"s{index}", "status": "pending"})
        for index, step in enumerate(plan.steps, start=1)
    ]
    return plan.model_copy(update={"steps": steps})


def estimate(plan: Plan, budget: ModeBudget, rewrites: int = 2, rounds: int = 2) -> Estimate:
    """Model calls for the whole run, "about `typical`, at most `max`" (research §R4).

    Typical: router + planner + retriever + analyst + verifier + writer, no rewrite and no
    revision. Most: retrieval with every rewrite, and one more planner → writer pass per
    allowed revision (`rounds - 1`: the last verification round ends the run). Both are
    capped at the budget, which the guard enforces anyway.
    """
    return Estimate(
        typical=min(_typical(), budget.max_model_calls),
        max=min(_most(rewrites, rounds), budget.max_model_calls),
    )


def _typical() -> int:
    return (
        ROUTER_CALLS
        + PLANNER_CALLS
        + RETRIEVER_CALLS
        + ANALYST_CALLS
        + VERIFIER_CALLS
        + WRITER_CALLS
    )


def _pass(rewrites: int) -> int:
    return (
        PLANNER_CALLS + RETRIEVER_CALLS + rewrites + ANALYST_CALLS + VERIFIER_CALLS + WRITER_CALLS
    )


def _most(rewrites: int, rounds: int) -> int:
    return ROUTER_CALLS + _pass(rewrites) * max(rounds, 1)


def calls_after_approval(rewrites: int = 2, rounds: int = 2) -> int:
    """The most calls a run can make once its plan is approved."""
    return _most(rewrites, rounds) - ROUTER_CALLS - PLANNER_CALLS


def parse_edit(data: Mapping[str, Any]) -> Plan | list[Reason]:
    """An edited plan from the dialog, or the field reasons it fails the schema with."""
    try:
        return Plan.model_validate(data)
    except ValidationError as exc:
        return reasons_from(exc, data)


def reasons_from(exc: ValidationError, data: Mapping[str, Any]) -> list[Reason]:
    steps = data.get("steps") if isinstance(data, Mapping) else None
    reasons = []
    for error in exc.errors(include_input=False, include_url=False):
        loc = list(error["loc"])
        step: str | None = None
        if len(loc) >= 2 and loc[0] == "steps" and isinstance(loc[1], int):
            raw = steps[loc[1]] if isinstance(steps, list) and loc[1] < len(steps) else None
            step = str(raw.get("id")) if isinstance(raw, Mapping) and raw.get("id") else None
            step = step or f"s{loc[1] + 1}"
            loc = loc[2:]
        field = ".".join(str(part) for part in loc if not isinstance(part, int)) or "plan"
        reasons.append(Reason(step=step, field=field, reason=error["msg"]))
    return reasons


def validate_edit(
    plan: Plan,
    budget: ModeBudget,
    calls_used: int,
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
    rewrites: int = 2,
    rounds: int = 2,
) -> list[Reason]:
    """Deterministic checks of an edited (already schema-valid) plan; empty when it is fine."""
    reasons = []
    if len(plan.steps) > max_steps:
        reasons.append(
            Reason(step=None, field="steps", reason=f"at most {max_steps} steps are allowed")
        )
    for step in plan.steps:
        if is_advice_request(step.goal):
            reasons.append(
                Reason(
                    step=step.id,
                    field="goal",
                    reason="asks for investment advice, which Disclosly does not give",
                )
            )
    calls_left = budget.max_model_calls - calls_used
    if calls_after_approval(rewrites, rounds) > calls_left:
        reasons.append(
            Reason(
                step=None,
                field="estimate",
                reason=f"the plan may need more model calls than the {calls_left} left",
            )
        )
    return reasons


def within_scope(revised: Plan, approved: Plan) -> bool:
    """A verifier revision may only use the tickers, periods and step kinds already approved."""
    return (
        revised.tickers() <= approved.tickers()
        and revised.periods() <= approved.periods()
        and revised.kinds() <= approved.kinds()
    )
