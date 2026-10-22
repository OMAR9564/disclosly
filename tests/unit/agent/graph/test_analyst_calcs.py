"""analyst calculations (T035): inputs resolved in code, one sandbox run per round."""

import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import pytest

from agent.budget import LimitReached, ModeBudget, Usage
from agent.graph import calculations
from agent.graph.nodes.analyst import analyst_node, task_class
from agent.graph.state import Calculation, CalculationInput, ResearchState
from agent.plan import Plan
from llm.types import ChatResponse
from sandbox.runner import run as runner
from tests.agent_fakes import ScriptedRouter
from tests.unit.agent.graph.helpers import Events, make_ctx, make_deps
from tools.python import SandboxRejected, SandboxRun
from tools.types import SandboxUnavailable, SeenItem

NET_DEBT = SeenItem(
    cite_id="f:88",
    text="Net borç (2025-Q4): 41,2 milyar TRY",
    value=Decimal("41.2"),
    scale=Decimal(10) ** 9,
    source={"kind": "fact"},
)
EBITDA = SeenItem(
    cite_id="f:91",
    text="FAVÖK (2025-Q4): 20,6 milyar TRY",
    value=Decimal("20.6"),
    scale=Decimal(10) ** 9,
    source={"kind": "fact"},
)
CHUNK = SeenItem(
    cite_id="c:abc",
    text="Şirketin nakit varlıkları 15,6 milyar TL oldu.",
    source={"kind": "chunk"},
)
PLAN = Plan.model_validate(
    {
        "steps": [
            {"id": "s1", "kind": "facts", "goal": "Net borç ve FAVÖK", "tickers": ["THYAO"]},
            {"id": "s2", "kind": "calculate", "goal": "Net borç / FAVÖK", "tickers": ["THYAO"]},
        ]
    }
)
RATIO = {
    "name": "net_debt_to_ebitda",
    "description": "Net borç / FAVÖK",
    "inputs": [
        # A model-typed value for a fact input is ignored.
        {"name": "net_debt", "cite_id": "f:88", "value": "999"},
        {"name": "ebitda", "cite_id": "f:91"},
    ],
    "code": "result = inputs['net_debt'] / inputs['ebitda']",
}


class LocalSandbox:
    """Runs the batch with the real runner in-process (no Docker) and counts the runs."""

    def __init__(self, *, fail: str | None = None) -> None:
        self.runs: list[tuple[str, dict[str, Decimal], float]] = []
        self.fail = fail

    async def run(
        self, code: str, inputs: Mapping[str, Decimal], *, timeout_s: float
    ) -> SandboxRun:
        self.runs.append((code, dict(inputs), timeout_s))
        if self.fail == "unavailable":
            raise SandboxUnavailable("down")
        if self.fail == "rejected":
            raise SandboxRejected("too big")
        if self.fail == "timeout":
            return SandboxRun(ok=False, error="timed out after 20 s", timed_out=True)
        answer = runner.execute(code, {k: float(v) for k, v in inputs.items()})
        return SandboxRun(
            ok=answer["error"] is None,
            outputs={k: Decimal(v) for k, v in answer["outputs"].items()},
            stdout=answer["stdout"],
            error=answer["error"],
        )


def _reply(*calcs: dict[str, Any], findings: list[dict[str, Any]] | None = None) -> ChatResponse:
    body = {
        "findings": findings
        or [
            {
                "id": "F1",
                "text": "Net borç ve FAVÖK raporlanmış.",
                "cite_ids": ["f:88", "f:91"],
                "calcs": ["net_debt_to_ebitda"],
            }
        ],
        "calculations": list(calcs),
    }
    return ChatResponse(content=json.dumps(body, ensure_ascii=False), provider="g", model="m")


def _state(**fields: Any) -> ResearchState:
    state: ResearchState = {
        "question": "THYAO net borç / FAVÖK oranı nedir?",
        "language": "tr",
        "plan": PLAN,
        "evidence": [NET_DEBT, EBITDA, CHUNK],
        "budget": ModeBudget.deep(),
        "usage": Usage(model_calls=3),
    }
    state.update(fields)  # type: ignore[typeddict-item]
    return state


async def _run(
    reply: ChatResponse, sandbox: Any, events: Events | None = None, **fields: Any
) -> dict[str, Any]:
    deps = make_deps(ScriptedRouter([reply]), events, sandbox=sandbox)
    ctx = make_ctx(deps, "analyst", budget=ModeBudget.deep(), usage=Usage(model_calls=3))
    return await analyst_node(_state(**fields), ctx)


async def test_fact_inputs_take_the_stored_value() -> None:
    sandbox = LocalSandbox()
    events = Events()
    update = await _run(_reply(RATIO), sandbox, events)

    [calc] = update["calculations"]
    assert calc.cite_id == "k:1"
    assert calc.inputs == [
        CalculationInput(name="net_debt", cite_id="f:88", value=Decimal("41200000000")),
        CalculationInput(name="ebitda", cite_id="f:91", value=Decimal("20600000000")),
    ]
    assert calc.output == Decimal("2.0")
    assert (calc.error, calc.verified) == (None, False)
    assert len(sandbox.runs) == 1
    assert events.data("calculation") == [
        {
            "cite_id": "k:1",
            "name": "net_debt_to_ebitda",
            "description": "Net borç / FAVÖK",
            "status": "ok",
            "node": "analyst",
            "attempt": 1,
        }
    ]
    assert update["findings"][0].calcs == ["net_debt_to_ebitda"]


async def test_chunk_input_needs_a_number_from_the_text() -> None:
    cash_ok = {
        "name": "cash_share",
        "description": "Nakit / net borç",
        "inputs": [
            {"name": "cash", "cite_id": "c:abc", "value": "15600000000"},
            {"name": "net_debt", "cite_id": "f:88"},
        ],
        "code": "result = inputs['cash'] / inputs['net_debt']",
    }
    cash_wrong = {**cash_ok, "name": "cash_wrong"}
    cash_wrong["inputs"] = [
        {"name": "cash", "cite_id": "c:abc", "value": "16000000000"},
        {"name": "net_debt", "cite_id": "f:88"},
    ]
    no_value = {**cash_ok, "name": "cash_missing", "inputs": [{"name": "cash", "cite_id": "c:abc"}]}
    sandbox = LocalSandbox()

    update = await _run(_reply(cash_ok, cash_wrong, no_value), sandbox)

    ok, wrong, missing = update["calculations"]
    assert ok.error is None
    assert ok.output is not None and abs(ok.output - Decimal("15.6") / Decimal("41.2")) < 1e-12
    assert wrong.error == "input cash: 16000000000 is not in c:abc"
    assert missing.error == "input cash: a chunk input needs the value it reads"
    assert (wrong.output, missing.output) == (None, None)
    # Rejected calculations never reach the sandbox; the valid one still runs once.
    assert len(sandbox.runs) == 1
    assert "cash_wrong" not in sandbox.runs[0][0]


async def test_one_failing_calculation_does_not_stop_the_round() -> None:
    broken = {**RATIO, "name": "broken", "code": "result = inputs['net_debt'] / 0"}
    no_result = {**RATIO, "name": "no_result", "code": "x = 1"}
    sandbox = LocalSandbox()
    events = Events()

    update = await _run(_reply(RATIO, broken, no_result), sandbox, events)

    ratio, failed, missing = update["calculations"]
    assert ratio.output == Decimal("2.0")
    assert failed.error == "ZeroDivisionError: float division by zero"
    assert missing.error == "NameError: name 'result' is not defined"
    assert [c.cite_id for c in update["calculations"]] == ["k:1", "k:2", "k:3"]
    assert [d["status"] for d in events.data("calculation")] == ["ok", "error", "error"]
    assert len(sandbox.runs) == 1


async def test_sandbox_errors_are_recorded() -> None:
    update = await _run(_reply(RATIO), LocalSandbox(fail="timeout"))
    [calc] = update["calculations"]
    assert (calc.output, calc.error) == (None, "timed out after 20 s")

    update = await _run(_reply(RATIO), LocalSandbox(fail="rejected"))
    assert update["calculations"][0].error == "too big"


async def test_unavailable_sandbox_raises_for_the_retry_policy() -> None:
    with pytest.raises(SandboxUnavailable):
        await _run(_reply(RATIO), LocalSandbox(fail="unavailable"))
    with pytest.raises(SandboxUnavailable):
        await _run(_reply(RATIO), None)


async def test_ids_continue_after_earlier_rounds_and_budget_counts_runs() -> None:
    earlier = Calculation(cite_id="k:1", name="old", code="result = 1", output=Decimal(1))
    sandbox = LocalSandbox()
    deps = make_deps(ScriptedRouter([_reply(RATIO)]), sandbox=sandbox)
    usage = Usage(model_calls=3)
    ctx = make_ctx(deps, "analyst", budget=ModeBudget.deep(), usage=usage)

    update = await analyst_node(_state(calculations=[earlier]), ctx)

    assert update["calculations"][0].cite_id == "k:2"
    assert usage.sandbox_runs == 1


async def test_sandbox_budget_limit_stops_the_node() -> None:
    deps = make_deps(ScriptedRouter([_reply(RATIO)]), sandbox=LocalSandbox())
    budget = ModeBudget.deep()
    usage = Usage(model_calls=3, sandbox_runs=budget.max_sandbox_runs)
    ctx = make_ctx(deps, "analyst", budget=budget, usage=usage)
    with pytest.raises(LimitReached):
        await analyst_node(_state(), ctx)


async def test_no_calculations_no_sandbox_run() -> None:
    sandbox = LocalSandbox()
    update = await _run(_reply(), sandbox)
    assert update["calculations"] == []
    assert sandbox.runs == []


def test_task_class_is_strong_with_a_calculate_step() -> None:
    assert task_class(PLAN) == "strong"


def test_resolve_rejects_duplicates_unknown_ids_and_syntax_errors() -> None:
    specs = [
        calculations.CalculationSpec.model_validate(RATIO),
        calculations.CalculationSpec.model_validate(RATIO),
        calculations.CalculationSpec.model_validate(
            {**RATIO, "name": "unknown", "inputs": [{"name": "x", "cite_id": "f:404"}]}
        ),
        calculations.CalculationSpec.model_validate(
            {**RATIO, "name": "same_input", "inputs": [RATIO["inputs"][1], RATIO["inputs"][1]]}
        ),
        calculations.CalculationSpec.model_validate({**RATIO, "name": "syntax", "code": "def ("}),
        calculations.CalculationSpec.model_validate(
            {**RATIO, "name": "from_calc", "inputs": [{"name": "k", "cite_id": "k:1"}]}
        ),
    ]
    evidence = {
        "f:88": NET_DEBT,
        "f:91": EBITDA,
        "k:1": SeenItem(cite_id="k:1", text="x", source={}),
        "f:92": SeenItem(cite_id="f:92", text="no value", source={}),
    }
    first, twice, unknown, same, syntax, from_calc = calculations.resolve(specs, evidence)
    assert first.error is None
    assert twice.error == "calculation net_debt_to_ebitda is defined twice"
    assert unknown.error == "input x: f:404 is not in the evidence"
    assert same.error == "input names must be unique"
    assert syntax.error is not None and syntax.error.startswith("SyntaxError")
    assert from_calc.error == "input k: k:1 cannot be a calculation input"
    no_value = calculations.CalcInputSpec(name="v", cite_id="f:92")
    assert calculations.resolve_input(no_value, evidence) == "input v: f:92 has no value"


async def test_a_round_over_16_kib_drops_what_does_not_fit() -> None:
    padding = "#" + "x" * 3900 + "\n"
    big = [{**RATIO, "name": f"calc_{i}", "code": padding + "result = 1"} for i in range(5)]
    sandbox = LocalSandbox()
    update = await _run(_reply(*big), sandbox)
    errors = [c.error for c in update["calculations"]]
    assert errors[:3] == [None, None, None]
    assert errors[3:] == ["the calculations of one round exceed 16 KiB of code"] * 2
    assert len(sandbox.runs) == 1


def test_batch_errors_ignore_other_output() -> None:
    stdout = 'hello\n{"calc": "a", "error": "boom"}\n{"x": 1}\n[1]\n'
    assert calculations.batch_errors(stdout) == {"a": "boom"}
