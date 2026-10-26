"""verifier: check findings and calculations before the writer sees them (research §R10).

Three checks, in order:

1. Inputs (code): every input of the round's calculations is read again from its citation (a
   fact's stored value and scale, a chunk's stored text) and must give the same value.
2. Recompute (sandbox): the calculations whose inputs held run again in one fresh sandbox
   execution; each output must equal the recorded one within `max(1e-9 * |value|, 0.005)`
   (0.1 for a percentage).
3. Claims: every number in a finding must be found in its cited evidence or a verified
   calculation (code), and one `strong` call judges whether each finding is supported by the
   cited excerpts, wrapped as untrusted data.

Any failure counts a round. While rounds are left the graph goes back to the planner; after
the last one the failing findings and calculations are dropped, listed in `unverified`, and the
writer marks the report partial.
"""

import json
import re
from collections import Counter
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.answer import Language, states_amount, unsupported_figures
from agent.graph.calculations import batch
from agent.graph.context import NodeContext
from agent.graph.state import (
    Calculation,
    CalculationInput,
    Finding,
    ResearchState,
    VerificationFailure,
    VerificationResult,
)
from agent.prompts import node_prompt, with_schema
from agent.report import calculation_item
from agent.telemetry import verify_span
from llm.types import ChatMessage, ChatRequest
from tools.python import SandboxRejected, run_python
from tools.registry import wrap
from tools.types import SandboxUnavailable, SeenItem

MAX_TOKENS = 1500
EVIDENCE_ITEM_CHARS = 1200
RELATIVE_TOLERANCE = Decimal("1e-9")
ABSOLUTE_TOLERANCE = Decimal("0.005")
PERCENT_TOLERANCE = Decimal("0.1")
_PERCENT = re.compile(r"%|percent|yüzde|pct", re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")

FailureKind = Literal["input_mismatch", "recompute_mismatch", "unsupported_claim", "sandbox_error"]


class ClaimCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str
    supported: bool
    reason: str = Field(default="", max_length=300)


class ClaimOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[ClaimCheck]


def tolerance(calc: Calculation, value: Decimal) -> Decimal:
    floor = (
        PERCENT_TOLERANCE
        if _PERCENT.search(f"{calc.name} {calc.description}")
        else (ABSOLUTE_TOLERANCE)
    )
    return max(RELATIVE_TOLERANCE * abs(value), floor)


def same_output(calc: Calculation, recomputed: Decimal) -> bool:
    if calc.output is None:
        return False
    return abs(calc.output - recomputed) <= tolerance(calc, calc.output)


def check_input(item: CalculationInput, source: SeenItem | None) -> str | None:
    """Why the input no longer holds against its citation, or None."""
    if source is None:
        return f"{item.cite_id} could not be read again"
    if item.cite_id.startswith("f:"):
        if source.value is None or source.value * (source.scale or 1) != item.value:
            return f"{item.name} differs from the stored value of {item.cite_id}"
        return None
    if not states_amount(source, item.value):
        return f"{item.name} = {item.value} is not in {item.cite_id}"
    return None


class Checks:
    """Failures collected over one verification round."""

    def __init__(self) -> None:
        self.failures: list[VerificationFailure] = []
        self.failed_calcs: set[str] = set()
        self.failed_findings: set[str] = set()

    def calc(self, kind: FailureKind, calc: Calculation, reason: str) -> None:
        self.failures.append(VerificationFailure(kind=kind, ref=calc.cite_id, reason=reason))
        self.failed_calcs.add(calc.name)

    def finding(self, finding: Finding, reason: str) -> None:
        if finding.id not in self.failed_findings:
            self.failures.append(
                VerificationFailure(kind="unsupported_claim", ref=finding.id, reason=reason[:300])
            )
            self.failed_findings.add(finding.id)


async def _reload(
    ctx: NodeContext, cite_ids: list[str], evidence: Mapping[str, SeenItem]
) -> dict[str, SeenItem]:
    reloader = ctx.deps.citations
    if reloader is None:
        return {c: evidence[c] for c in cite_ids if c in evidence}
    return await reloader.reload(cite_ids)


async def _check_calculations(
    state: ResearchState, ctx: NodeContext, checks: Checks
) -> list[Calculation]:
    """Inputs and recompute for the round's unchecked calculations; returns them updated."""
    evidence = {item.cite_id: item for item in state.get("evidence") or []}
    pending = [c for c in state.get("calculations") or [] if not c.checked]
    sources = await _reload(ctx, [i.cite_id for c in pending for i in c.inputs], evidence)
    rerun: list[Calculation] = []
    for calc in pending:
        if calc.error is not None or calc.output is None:
            checks.calc("sandbox_error", calc, calc.error or "no result")
            continue
        problems = [p for i in calc.inputs if (p := check_input(i, sources.get(i.cite_id)))]
        if problems:
            checks.calc("input_mismatch", calc, "; ".join(problems)[:300])
        else:
            rerun.append(calc)

    recomputed: dict[str, Decimal] = {}
    run_error: str | None = None
    if rerun:
        if ctx.deps.sandbox is None:
            raise SandboxUnavailable("no calculation sandbox is configured")
        code, values = batch([(c.name, c.code, c.inputs) for c in rerun])
        try:
            result = await run_python(
                ctx.deps.sandbox,
                code,
                values,
                usage=ctx.usage,
                budget=ctx.budget,
                deadline_at=ctx.deadline_at,
                clock=ctx.deps.clock,
            )
        except SandboxRejected as exc:
            run_error = str(exc)
        else:
            recomputed = result.outputs
            run_error = None if result.ok else (result.error or "the sandbox run failed")
    for calc in rerun:
        value = recomputed.get(calc.name)
        if run_error is not None or value is None:
            checks.calc("sandbox_error", calc, run_error or "no result on the re-run")
        elif not same_output(calc, value):
            checks.calc("recompute_mismatch", calc, f"recorded {calc.output}, recomputed {value}")

    return [
        calc.model_copy(update={"checked": True, "verified": calc.name not in checks.failed_calcs})
        for calc in pending
    ]


def _claims_request(
    state: ResearchState,
    findings: list[Finding],
    items: Mapping[str, SeenItem],
    verified: list[Calculation],
    language: Language,
) -> ChatRequest:
    schema = ClaimOutput.model_json_schema()
    cited = dict.fromkeys(c for f in findings for c in f.cite_ids if c in items)
    documents = "\n".join(
        wrap(items[c].model_copy(update={"text": items[c].text[:EVIDENCE_ITEM_CHARS]}))
        for c in cited
        if not c.startswith("k:")
    )
    data = {
        "findings": [f.model_dump(include={"id", "text", "cite_ids", "calcs"}) for f in findings],
        "calculations": [
            {"name": c.name, "description": c.description, "output": str(c.output)}
            for c in verified
        ],
    }
    user = f"{state['question']}\n\n{json.dumps(data, ensure_ascii=False)}\n\n{documents}"
    return ChatRequest(
        messages=[
            ChatMessage(
                role="system", content=with_schema(node_prompt("verifier", language), schema)
            ),
            ChatMessage(role="user", content=user),
        ],
        response_schema=schema,
        max_tokens=MAX_TOKENS,
    )


def parse_claims(content: str | None) -> ClaimOutput | None:
    if not content:
        return None
    try:
        return ClaimOutput.model_validate_json(_FENCE.sub("", content.strip()))
    except ValidationError:
        return None


async def _check_findings(
    state: ResearchState,
    ctx: NodeContext,
    findings: list[Finding],
    calculations: list[Calculation],
    checks: Checks,
) -> None:
    language: Language = state.get("language") or "tr"
    items: dict[str, SeenItem] = {item.cite_id: item for item in state.get("evidence") or []}
    verified = [c for c in calculations if c.verified and c.output is not None]
    by_name = {c.name: c for c in verified}
    for calc in verified:
        items[calc.cite_id] = calculation_item(calc.cite_id, calc.name, calc.output)  # type: ignore[arg-type]

    to_judge: list[Finding] = []
    for finding in findings:
        failed = [
            name for name in finding.calcs if name in checks.failed_calcs or name not in by_name
        ]
        if failed:
            checks.finding(finding, f"relies on calculations that failed: {failed}")
            continue
        cited = [items[c] for c in finding.cite_ids if c in items]
        cited += [items[by_name[name].cite_id] for name in finding.calcs]
        if len(cited) < len(finding.cite_ids):
            checks.finding(finding, "cites evidence that is not in the run")
            continue
        missing = unsupported_figures(finding.text, language, cited, derived_ok=False)
        if missing:
            checks.finding(finding, f"numbers not in the cited sources: {missing}")
            continue
        to_judge.append(finding)

    if not to_judge:
        return
    response = await ctx.call(_claims_request(state, to_judge, items, verified, language), "strong")
    output = parse_claims(response.content)
    decided = {c.finding_id: c for c in output.claims} if output else {}
    for finding in to_judge:
        claim = decided.get(finding.id)
        if claim is None:
            checks.finding(finding, "the claim check gave no decision")
        elif not claim.supported:
            checks.finding(finding, claim.reason or "not supported by the cited sources")


async def verifier_node(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
    round_number = state.get("verify_rounds", 0) + 1
    with verify_span(round_number=round_number) as span:
        update, checks = await _verify(state, ctx, round_number)
        span.set_attribute("disclosly.verify.passed", not checks.failures)
        for kind, count in Counter(f.kind for f in checks.failures).items():
            span.set_attribute(f"disclosly.verify.failures.{kind}", count)
    return update


async def _verify(
    state: ResearchState, ctx: NodeContext, round_number: int
) -> tuple[dict[str, Any], Checks]:
    checks = Checks()
    checked = await _check_calculations(state, ctx, checks)
    known = {c.cite_id: c for c in state.get("calculations") or []}
    known.update({c.cite_id: c for c in checked})
    findings = state.get("findings") or []
    await _check_findings(state, ctx, findings, list(known.values()), checks)

    passed = not checks.failures
    result = VerificationResult(round=round_number, passed=passed, failures=checks.failures)
    update: dict[str, Any] = {"verification": result, "calculations": checked}
    if passed:
        next_node = "writer"
    else:
        update["verify_rounds"] = round_number
        if round_number < ctx.deps.verify_max_rounds:
            next_node = "planner"
        else:
            kept = [f for f in findings if f.id not in checks.failed_findings]
            dropped = [f.text for f in findings if f.id in checks.failed_findings]
            dropped += [c.description or c.name for c in checked if c.name in checks.failed_calcs]
            update["findings"] = kept
            update["unverified"] = [*(state.get("unverified") or []), *dropped]
            next_node = "writer" if kept else "finalize_partial"
    await ctx.emit(
        "verification",
        {
            "round": round_number,
            "passed": passed,
            "failures": len(checks.failures),
            "next": next_node,
        },
    )
    return update, checks
