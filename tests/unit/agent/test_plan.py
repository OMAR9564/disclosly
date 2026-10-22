"""agent.plan (T009): plan schema, estimate, edit validation and revision scope."""

from typing import Any

import pytest
from pydantic import ValidationError

from agent.budget import ModeBudget
from agent.plan import (
    Plan,
    PlanStep,
    calls_after_approval,
    estimate,
    parse_edit,
    plan_json_schema,
    renumber,
    validate_edit,
    within_scope,
)


def _step(**fields: Any) -> dict[str, Any]:
    step: dict[str, Any] = {
        "id": "s1",
        "kind": "facts",
        "goal": "THYAO ve PGSUS net borç ve FAVÖK, son 4 çeyrek",
        "tickers": ["THYAO", "PGSUS"],
        "periods": ["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"],
        "metrics": ["ebitda", "total_debt"],
    }
    step.update(fields)
    return step


def _plan(*steps: dict[str, Any]) -> Plan:
    return Plan.model_validate({"steps": list(steps) or [_step()]})


def test_valid_plan_defaults() -> None:
    plan = _plan(_step(), _step(id="s2", kind="calculate", goal="Net borç / FAVÖK oranı"))
    assert plan.steps[0].status == "pending"
    assert plan.estimate is None
    assert plan.edited is False
    assert plan.revision == 0
    assert plan.tickers() == {"THYAO", "PGSUS"}
    assert plan.kinds() == {"facts", "calculate"}


def test_tickers_upper_cased_and_deduplicated() -> None:
    step = PlanStep.model_validate(_step(tickers=["thyao", " THYAO", "aapl"]))
    assert step.tickers == ["THYAO", "AAPL"]


@pytest.mark.parametrize(
    "fields",
    [
        {"id": "step1"},
        {"id": "s0"},
        {"kind": "trade"},
        {"goal": ""},
        {"goal": "   "},
        {"goal": "x" * 301},
        {"tickers": ["A", "B", "C", "D", "E"]},
        {"tickers": ["THY AO"]},
        {"periods": ["2025-Q5"]},
        {"periods": ["Q3 2025"]},
        {"periods": [f"20{i:02d}-FY" for i in range(9)]},
        {"metrics": ["share_price"]},
        {"status": "skipped"},
        {"extra": 1},
    ],
)
def test_step_field_limits(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PlanStep.model_validate(_step(**fields))


def test_plan_needs_steps_and_unique_ids() -> None:
    with pytest.raises(ValidationError):
        Plan.model_validate({"steps": []})
    with pytest.raises(ValidationError):
        _plan(_step(), _step())


def test_estimate_fits_the_deep_budget() -> None:
    plan = _plan()
    result = estimate(plan, ModeBudget.deep())
    # router + planner + retriever + analyst + verifier + writer
    assert result.typical == 6
    # router + 2 passes of (planner + retriever + 2 rewrites + analyst + verifier + writer)
    assert result.max == 15
    assert result.max <= ModeBudget.deep().max_model_calls


def test_estimate_capped_at_the_budget() -> None:
    small = ModeBudget(mode="deep", max_model_calls=5, deadline_seconds=300, max_tool_calls=24)
    result = estimate(_plan(), small, rewrites=2, rounds=3)
    assert result.typical == 5
    assert result.max == 5


def test_calls_after_approval() -> None:
    assert calls_after_approval() == 13
    # retriever + analyst + verifier + writer
    assert calls_after_approval(rewrites=0, rounds=1) == 4


def test_valid_edit_has_no_reasons() -> None:
    assert validate_edit(_plan(), ModeBudget.deep(), calls_used=2) == []


def test_edit_with_too_many_steps() -> None:
    plan = _plan(*[_step(id=f"s{i}") for i in range(1, 10)])
    reasons = validate_edit(plan, ModeBudget.deep(), calls_used=2)
    assert [(r.step, r.field) for r in reasons] == [(None, "steps")]
    assert validate_edit(plan, ModeBudget.deep(), calls_used=2, max_steps=9) == []


@pytest.mark.parametrize(
    "goal", ["THYAO hissesini almalı mıyım?", "Should I buy AAPL after this?", "hedef fiyat ne"]
)
def test_advice_in_an_edited_goal_is_rejected(goal: str) -> None:
    plan = _plan(_step(), _step(id="s2", goal=goal))
    reasons = validate_edit(plan, ModeBudget.deep(), calls_used=2)
    assert [(r.step, r.field) for r in reasons] == [("s2", "goal")]


def test_edit_rejected_when_calls_left_are_too_few() -> None:
    reasons = validate_edit(_plan(), ModeBudget.deep(), calls_used=10)
    assert [r.field for r in reasons] == ["estimate"]


def test_unknown_tickers_are_allowed() -> None:
    plan = _plan(_step(tickers=["ZZZZ"]))
    assert validate_edit(plan, ModeBudget.deep(), calls_used=2) == []


def test_parse_edit_reports_field_reasons_per_step() -> None:
    data = {"steps": [_step(), _step(id="s2", periods=["2025-Q9"], goal="")]}
    reasons = parse_edit(data)
    assert isinstance(reasons, list)
    assert {(r.step, r.field) for r in reasons} == {("s2", "periods"), ("s2", "goal")}
    assert all(r.reason for r in reasons)

    plan = parse_edit({"steps": [_step()]})
    assert isinstance(plan, Plan)
    no_steps = parse_edit({"steps": []})
    assert isinstance(no_steps, list)
    assert no_steps[0].step is None
    assert no_steps[0].field == "steps"


def test_parse_edit_names_steps_without_ids_by_position() -> None:
    step = _step(kind="dance")
    del step["id"]
    reasons = parse_edit({"steps": [_step(), step]})
    assert isinstance(reasons, list)
    assert {r.step for r in reasons} == {"s2"}


def test_renumber_resets_ids_and_statuses() -> None:
    plan = _plan(_step(id="s4", status="done"), _step(id="s9"))
    assert [(s.id, s.status) for s in renumber(plan).steps] == [
        ("s1", "pending"),
        ("s2", "pending"),
    ]


def test_within_scope() -> None:
    approved = _plan(_step(), _step(id="s2", kind="search", goal="Faaliyet raporu riskleri"))
    narrower = _plan(_step(tickers=["THYAO"], periods=["2025-Q4"]))
    assert within_scope(narrower, approved)
    assert not within_scope(_plan(_step(tickers=["AAPL"])), approved)
    assert not within_scope(_plan(_step(periods=["2024-Q4"])), approved)
    assert not within_scope(_plan(_step(kind="calculate")), approved)


def test_plan_json_schema_leaves_code_fields_out() -> None:
    schema = plan_json_schema()
    assert set(schema["properties"]) == {"steps"}
