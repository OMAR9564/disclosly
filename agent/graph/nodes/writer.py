"""writer: one `strong` call for the `Report`, checked in code (contracts/report.md).

The writer sees the checked findings, the evidence they cite and the verified calculations;
nothing else reaches the report. `validate_report` runs on the reply, with one re-ask while a
call is left; a report that still fails keeps only its checked parts (`to_partial`), and a
reply that never parses leaves no draft, so the graph goes to `finalize_partial`. A run whose
retrieval found nothing gets a `no_evidence` report without a model call.
"""

import json
import re
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from agent.answer import Language
from agent.graph.context import NodeContext
from agent.graph.state import Calculation, Finding, ResearchState
from agent.prompts import node_prompt, with_schema
from agent.report import (
    MAX_UNVERIFIED,
    NOTE_MAX_CHARS,
    Confidence,
    Report,
    ReportValidation,
    finalize_report,
    report_json_schema,
    to_partial,
    validate_report,
)
from llm.types import ChatMessage, ChatRequest
from tools.registry import wrap
from tools.types import SeenItem

MAX_TOKENS = 3000
EVIDENCE_ITEM_CHARS = 1500
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_REASK = {
    "tr": "Rapor doğrulamadan geçmedi. Şu hataları düzelt ve JSON'u yeniden ver:\n",
    "en": "The report failed validation. Fix these errors and return the JSON again:\n",
}
NO_EVIDENCE: dict[Language, str] = {
    "tr": "Plandaki şirketler ve dönemler için kaynaklarda ilgili bilgi bulunamadı.",
    "en": "No relevant information was found in the sources for the plan's companies and periods.",
}
CONFIDENCE_REQUIRED = "confidence: set level and note"
UNVERIFIED_NOTE: dict[Language, str] = {
    "tr": "Bazı bulgular ve hesaplamalar doğrulanamadı ve rapordan çıkarıldı.",
    "en": "Some findings and calculations could not be verified and were left out.",
}


def with_unverified(report: Report, unverified: list[str], language: Language) -> Report:
    """After the last failed verification round: a partial report with low confidence that
    lists what the verifier dropped (contracts/report.md rule 5)."""
    if not unverified or report.status in ("refused", "no_evidence"):
        return report
    note = report.confidence.note if report.confidence and report.confidence.note else ""
    note = f"{UNVERIFIED_NOTE[language]} {note}".strip()[:NOTE_MAX_CHARS]
    return report.model_copy(
        update={
            "status": "partial",
            "confidence": Confidence(level="low", note=note),
            "unverified": list(dict.fromkeys([*report.unverified, *unverified]))[:MAX_UNVERIFIED],
        }
    )


def parse_report(content: str | None) -> tuple[Report | None, list[str]]:
    if not content:
        return None, ["empty response; return the Report JSON"]
    try:
        return Report.model_validate_json(_FENCE.sub("", content.strip())), []
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(p) for p in e['loc']) or 'report'}: {e['msg']}"
            for e in exc.errors(include_input=False)[:10]
        ]


def verified_outputs(calculations: list[Calculation]) -> dict[str, Decimal]:
    return {c.cite_id: c.output for c in calculations if c.verified and c.output is not None}


def _material(
    findings: list[Finding], evidence: dict[str, SeenItem], calculations: list[Calculation]
) -> str:
    cited = dict.fromkeys(c for f in findings for c in f.cite_ids if c in evidence)
    documents = "\n".join(
        wrap(evidence[c].model_copy(update={"text": evidence[c].text[:EVIDENCE_ITEM_CHARS]}))
        for c in cited
    )
    calcs = [
        {
            "cite_id": c.cite_id,
            "name": c.name,
            "description": c.description,
            "output": str(c.output),
        }
        for c in calculations
        if c.verified and c.output is not None
    ]
    data = {
        "findings": [f.model_dump(include={"id", "text", "cite_ids", "calcs"}) for f in findings],
        "calculations": calcs,
    }
    return json.dumps(data, ensure_ascii=False) + "\n\n" + documents


async def writer_node(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
    language: Language = state.get("language") or "tr"
    findings = state.get("findings") or []
    evidence = {item.cite_id: item for item in state.get("evidence") or []}
    calculations = state.get("calculations") or []
    if not findings and not evidence:
        empty = Report(language=language, status="no_evidence", summary=NO_EVIDENCE[language])
        return {"draft": finalize_report(empty)}

    outputs = verified_outputs(calculations)
    dropped = state.get("unverified") or []
    plan = state.get("plan")
    plan_steps = (
        [s.model_dump(include={"id", "goal", "status"}) for s in plan.steps] if plan else []
    )
    schema = report_json_schema()
    user = (
        f"{state['question']}\n\n"
        + json.dumps({"plan": plan_steps}, ensure_ascii=False)
        + "\n\n"
        + _material(findings, evidence, calculations)
    )
    messages = [
        ChatMessage(role="system", content=with_schema(node_prompt("writer", language), schema)),
        ChatMessage(role="user", content=user),
    ]
    last: tuple[Report, ReportValidation] | None = None
    limit_hit = state.get("limit_hit") is not None
    for attempt in range(2):
        if attempt and ctx.usage.calls_left(ctx.budget) < 1:
            break
        response = await ctx.call(
            ChatRequest(messages=messages, response_schema=schema, max_tokens=MAX_TOKENS),
            "strong",
        )
        report, errors = parse_report(response.content)
        if report is not None:
            validation = validate_report(report, evidence, outputs, language, limit_hit=limit_hit)
            if report.confidence is None and report.status != "refused":
                validation.errors.append(CONFIDENCE_REQUIRED)
            if validation.ok:
                return {"draft": finalize_report(with_unverified(report, dropped, language))}
            last = (report, validation)
            errors = validation.errors
        messages = [
            *messages,
            ChatMessage(role="assistant", content=response.content or ""),
            ChatMessage(role="user", content=_REASK[language] + "\n".join(errors)),
        ]

    if last is None:
        return {"draft": None}
    partial = to_partial(last[0], last[1], language)
    return {"draft": finalize_report(with_unverified(partial, dropped, language))}
