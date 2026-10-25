"""verifier node (T036): input re-check, recompute, claim support, rounds and routing."""

import json
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

import pytest

from agent.budget import ModeBudget, Usage
from agent.graph.build import Routes
from agent.graph.nodes.verifier import (
    check_input,
    parse_claims,
    same_output,
    tolerance,
    verifier_node,
)
from agent.graph.nodes.writer import with_unverified
from agent.graph.state import (
    Calculation,
    CalculationInput,
    Finding,
    ResearchState,
    VerificationResult,
    merge_calculations,
)
from agent.report import Confidence, Report
from llm.types import ChatResponse
from tests.agent_fakes import ScriptedRouter
from tests.unit.agent.graph.helpers import Events, make_ctx, make_deps
from tests.unit.agent.graph.test_analyst_calcs import EBITDA, NET_DEBT, LocalSandbox
from tools.python import SandboxRun
from tools.types import SandboxUnavailable, SeenItem

CALC = Calculation(
    cite_id="k:1",
    name="net_debt_to_ebitda",
    description="Net borç / FAVÖK",
    inputs=[
        CalculationInput(name="net_debt", cite_id="f:88", value=Decimal("41200000000")),
        CalculationInput(name="ebitda", cite_id="f:91", value=Decimal("20600000000")),
    ],
    code="result = inputs['net_debt'] / inputs['ebitda']",
    output=Decimal("2.0"),
)
F1 = Finding(
    id="F1",
    text="Net borç 41,2 milyar TL, FAVÖK 20,6 milyar TL.",
    cite_ids=["f:88", "f:91"],
    calcs=["net_debt_to_ebitda"],
)
F2 = Finding(id="F2", text="FAVÖK 20,6 milyar TL.", cite_ids=["f:91"])


async def _noop(state: ResearchState, ctx: Any) -> dict[str, Any]:
    return {}


ROUTES = Routes({"planner": _noop, "writer": _noop}, 2)


def _claims(*decisions: tuple[str, bool]) -> ChatResponse:
    body = {"claims": [{"finding_id": f, "supported": ok, "reason": "x"} for f, ok in decisions]}
    return ChatResponse(content=json.dumps(body), provider="g", model="m")


class Store:
    def __init__(self, items: Mapping[str, SeenItem]) -> None:
        self.items = dict(items)
        self.asked: list[str] = []

    async def reload(self, cite_ids: Iterable[str]) -> dict[str, SeenItem]:
        ids = list(cite_ids)
        self.asked += ids
        return {c: self.items[c] for c in ids if c in self.items}


class FixedSandbox(LocalSandbox):
    """Answers every recompute with fixed outputs."""

    def __init__(self, outputs: dict[str, Decimal]) -> None:
        super().__init__()
        self.outputs = outputs

    async def run(
        self, code: str, inputs: Mapping[str, Decimal], *, timeout_s: float
    ) -> SandboxRun:
        self.runs.append((code, dict(inputs), timeout_s))
        return SandboxRun(ok=True, outputs=self.outputs)


def _state(**fields: Any) -> ResearchState:
    state: ResearchState = {
        "question": "THYAO net borç / FAVÖK?",
        "language": "tr",
        "evidence": [NET_DEBT, EBITDA],
        "findings": [F1, F2],
        "calculations": [CALC],
        "verify_rounds": 0,
    }
    state.update(fields)  # type: ignore[typeddict-item]
    return state


async def _verify(
    replies: list[ChatResponse],
    *,
    sandbox: Any = None,
    store: Store | None = None,
    events: Events | None = None,
    usage: Usage | None = None,
    **fields: Any,
) -> tuple[dict[str, Any], ScriptedRouter]:
    router = ScriptedRouter(replies)
    deps = make_deps(
        router,
        events,
        sandbox=sandbox if sandbox is not None else LocalSandbox(),
        citations=store,
    )
    ctx = make_ctx(deps, "verifier", budget=ModeBudget.deep(), usage=usage or Usage())
    return await verifier_node(_state(**fields), ctx), router


async def test_everything_holds() -> None:
    events = Events()
    store = Store({"f:88": NET_DEBT, "f:91": EBITDA})
    sandbox = LocalSandbox()
    update, router = await _verify(
        [_claims(("F1", True), ("F2", True))], sandbox=sandbox, store=store, events=events
    )

    assert update["verification"] == VerificationResult(round=1, passed=True)
    [calc] = update["calculations"]
    assert (calc.checked, calc.verified) == (True, True)
    assert "verify_rounds" not in update
    assert store.asked == ["f:88", "f:91"]
    assert len(sandbox.runs) == 1
    task, request = router.calls[0]
    assert task == "strong"
    user = request.messages[1].content or ""
    assert '<untrusted_document cite_id="f:88">' in user
    assert '"output": "2.0"' in user
    assert events.data("verification") == [
        {
            "round": 1,
            "passed": True,
            "failures": 0,
            "next": "writer",
            "node": "verifier",
            "attempt": 1,
        }
    ]


async def test_changed_fact_fails_the_input_check() -> None:
    moved = NET_DEBT.model_copy(update={"value": Decimal("40")})
    update, router = await _verify(
        [_claims(("F2", True))], store=Store({"f:88": moved, "f:91": EBITDA})
    )
    result = update["verification"]
    assert not result.passed
    assert [(f.kind, f.ref) for f in result.failures] == [
        ("input_mismatch", "k:1"),
        ("unsupported_claim", "F1"),
    ]
    assert result.failures[0].reason == "net_debt differs from the stored value of f:88"
    assert update["calculations"][0].verified is False
    # F1 relies on the failed calculation: judged in code, not sent to the model.
    request = router.calls[0][1]
    assert '"F1"' not in (request.messages[1].content or "")


async def test_wrong_recorded_output_fails_the_recompute() -> None:
    update, _ = await _verify(
        [_claims(("F2", True))], sandbox=FixedSandbox({"net_debt_to_ebitda": Decimal("2.4")})
    )
    kinds = [f.kind for f in update["verification"].failures]
    assert kinds == ["recompute_mismatch", "unsupported_claim"]
    assert update["verification"].failures[0].reason == "recorded 2.0, recomputed 2.4"


async def test_sandbox_problems_fail_the_calculation() -> None:
    errored = CALC.model_copy(update={"error": "ZeroDivisionError", "output": None})
    update, _ = await _verify([_claims(("F2", True))], calculations=[errored])
    assert update["verification"].failures[0].kind == "sandbox_error"
    assert update["verification"].failures[0].reason == "ZeroDivisionError"

    class Broken(LocalSandbox):
        async def run(self, code: str, inputs: Any, *, timeout_s: float) -> SandboxRun:
            return SandboxRun(ok=False, error="timed out after 20 s", timed_out=True)

    update, _ = await _verify([_claims(("F2", True))], sandbox=Broken())
    assert update["verification"].failures[0].reason == "timed out after 20 s"

    with pytest.raises(SandboxUnavailable):
        await _verify([], sandbox=LocalSandbox(fail="unavailable"))


async def test_checked_calculations_are_not_run_again() -> None:
    sandbox = LocalSandbox()
    done = CALC.model_copy(update={"checked": True, "verified": True})
    update, _ = await _verify(
        [_claims(("F1", True), ("F2", True))], sandbox=sandbox, calculations=[done]
    )
    assert update["verification"].passed
    assert update["calculations"] == []
    assert sandbox.runs == []


async def test_numbers_not_in_sources_fail_without_a_model_call() -> None:
    wrong = Finding(id="F3", text="FAVÖK 99 milyar TL.", cite_ids=["f:91"])
    update, router = await _verify([], findings=[wrong], calculations=[])
    [failure] = update["verification"].failures
    assert (failure.kind, failure.ref) == ("unsupported_claim", "F3")
    assert "numbers not in the cited sources" in failure.reason
    assert router.calls == []


async def test_claim_judgement_and_missing_decisions() -> None:
    update, _ = await _verify([_claims(("F1", True), ("F2", False))])
    assert [f.ref for f in update["verification"].failures] == ["F2"]

    update, _ = await _verify([_claims(("F1", True))])
    assert update["verification"].failures[0].reason == "the claim check gave no decision"

    garbage = ChatResponse(content="not json", provider="g", model="m")
    update, _ = await _verify([garbage])
    assert len(update["verification"].failures) == 2


async def test_unknown_citations_and_calculations_fail() -> None:
    stray = Finding(id="F4", text="Borç arttı.", cite_ids=["f:404"])
    no_calc = Finding(id="F5", text="Oran hesaplandı.", cite_ids=["f:88"], calcs=["missing"])
    update, _ = await _verify([], findings=[stray, no_calc], calculations=[])
    reasons = [f.reason for f in update["verification"].failures]
    assert reasons == [
        "cites evidence that is not in the run",
        "relies on calculations that failed: ['missing']",
    ]


async def test_failure_with_rounds_left_goes_back_to_the_planner() -> None:
    events = Events()
    update, _ = await _verify([_claims(("F1", True), ("F2", False))], events=events)
    assert update["verify_rounds"] == 1
    assert "unverified" not in update
    assert events.data("verification")[0]["next"] == "planner"
    assert ROUTES.after_verifier({**_state(), **update}) == "planner"  # type: ignore[typeddict-item]


async def test_last_round_drops_what_failed_and_goes_to_the_writer() -> None:
    events = Events()
    moved = NET_DEBT.model_copy(update={"value": Decimal("40")})
    update, _ = await _verify(
        [_claims(("F2", True))],
        store=Store({"f:88": moved, "f:91": EBITDA}),
        events=events,
        verify_rounds=1,
        unverified=["earlier"],
    )
    assert update["verify_rounds"] == 2
    assert [f.id for f in update["findings"]] == ["F2"]
    assert update["unverified"] == ["earlier", F1.text, "Net borç / FAVÖK"]
    assert events.data("verification")[0]["next"] == "writer"
    assert ROUTES.after_verifier({**_state(), **update}) == "writer"  # type: ignore[typeddict-item]


async def test_last_round_with_nothing_left_goes_to_finalize_partial() -> None:
    events = Events()
    update, _ = await _verify(
        [_claims(("F1", False), ("F2", False))], events=events, verify_rounds=1
    )
    assert update["findings"] == []
    assert events.data("verification")[0]["next"] == "finalize_partial"
    assert ROUTES.after_verifier({**_state(), **update}) == "finalize_partial"  # type: ignore[typeddict-item]


async def test_without_a_store_the_run_evidence_is_used() -> None:
    update, _ = await _verify([_claims(("F1", True), ("F2", True))], store=None)
    assert update["verification"].passed


def test_input_check_and_tolerances() -> None:
    chunk = SeenItem(cite_id="c:a", text="Nakit 15,6 milyar TL.", source={})
    good = CalculationInput(name="cash", cite_id="c:a", value=Decimal("15600000000"))
    bad = good.model_copy(update={"value": Decimal("16000000000")})
    assert check_input(good, chunk) is None
    assert check_input(bad, chunk) == "cash = 16000000000 is not in c:a"
    assert check_input(good, None) == "c:a could not be read again"
    no_value = SeenItem(cite_id="f:1", text="", source={})
    assert check_input(CALC.inputs[0], no_value) is not None

    assert same_output(CALC, Decimal("2.004"))
    assert not same_output(CALC, Decimal("2.006"))
    big = CALC.model_copy(update={"output": Decimal("1e12")})
    assert same_output(big, Decimal("1e12") + 500)
    percent = CALC.model_copy(update={"description": "Marj (%)", "output": Decimal("12.3")})
    assert tolerance(percent, Decimal("12.3")) == Decimal("0.1")
    assert not same_output(CALC.model_copy(update={"output": None}), Decimal(1))
    assert parse_claims(None) is None


def test_merge_calculations_replaces_by_id() -> None:
    other = CALC.model_copy(update={"cite_id": "k:2"})
    checked = CALC.model_copy(update={"checked": True, "verified": True})
    merged = merge_calculations([CALC, other], [checked])
    assert merged == [checked, other]
    assert merge_calculations(None, [CALC]) == [CALC]


def test_writer_marks_the_report_partial_with_what_was_dropped() -> None:
    report = Report(
        language="tr",
        status="completed",
        summary="x",
        confidence=Confidence(level="high", note="Tamam."),
    )
    marked = with_unverified(report, ["F1 metni", "F1 metni"], "tr")
    assert marked.status == "partial"
    assert marked.confidence == Confidence(
        level="low",
        note="Bazı bulgular ve hesaplamalar doğrulanamadı ve rapordan çıkarıldı. Tamam.",
    )
    assert marked.unverified == ["F1 metni"]
    assert with_unverified(report, [], "tr") is report
    refused = report.model_copy(update={"status": "refused"})
    assert with_unverified(refused, ["x"], "tr") is refused
