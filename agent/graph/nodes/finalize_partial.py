"""finalize_partial: a `partial` report from checked material only (research §R3).

Reached when a limit is hit, a later node is not available, or the writer cannot produce a
report that passes the checks. It makes no model call. Findings stay only when every number
in them is found in their cited evidence or verified calculations; everything else is listed
as unverified. An advice question that reaches it gets a refused report.
"""

from typing import Any

from agent.advice import detect_language
from agent.answer import NO_VERIFIED_ANSWER, Language, Point, unsupported_figures
from agent.budget import LimitHit
from agent.graph.context import NodeContext
from agent.graph.state import ResearchState, evidence_by_id
from agent.report import MAX_UNVERIFIED, Confidence, Report, calculation_item

REFUSED: dict[Language, str] = {
    "tr": "Disclosly yatırım tavsiyesi vermez. Şirket açıklamalarındaki bilgileri "
    "sormak için soruyu yeniden yazın.",
    "en": "Disclosly does not give investment advice. Rephrase the question to ask about "
    "what the company disclosures say.",
}
LIMIT_NOTES: dict[Language, dict[LimitHit | None, str]] = {
    "tr": {
        "model_calls": "Araştırma model çağrısı sınırına ulaştı.",
        "deadline": "Araştırma süre sınırına ulaştı.",
        "steps": "Araştırma adım sınırına ulaştı.",
        "tool_calls": "Araştırma araç çağrısı sınırına ulaştı.",
        "sandbox_runs": "Araştırma hesaplama sınırına ulaştı.",
        None: "Araştırma tamamlanamadı.",
    },
    "en": {
        "model_calls": "The research reached its model call limit.",
        "deadline": "The research reached its time limit.",
        "steps": "The research reached its step limit.",
        "tool_calls": "The research reached its tool call limit.",
        "sandbox_runs": "The research reached its calculation limit.",
        None: "The research could not be completed.",
    },
}
VERIFIED_ONLY: dict[Language, str] = {
    "tr": "Yalnızca kaynaklarla doğrulanan bulgular gösteriliyor.",
    "en": "Only findings checked against the sources are shown.",
}


def partial_report(state: ResearchState) -> Report:
    language: Language = state.get("language") or detect_language(state.get("question", ""))
    if state.get("refused"):
        return Report(language=language, status="refused", summary=REFUSED[language])

    items = evidence_by_id(state.get("evidence") or [])
    for calc in state.get("calculations") or []:
        if calc.verified and calc.output is not None:
            items[calc.cite_id] = calculation_item(calc.cite_id, calc.name, calc.output)

    points: list[Point] = []
    unverified: list[str] = []
    for finding in state.get("findings") or []:
        cited = [items[c] for c in finding.cite_ids if c in items]
        if len(cited) == len(finding.cite_ids) and not unsupported_figures(
            finding.text, language, cited
        ):
            points.append(Point(text=finding.text, cite_ids=finding.cite_ids))
        else:
            unverified.append(finding.text)
    unverified += [
        calc.description or calc.name
        for calc in state.get("calculations") or []
        if not calc.verified
    ]

    note = LIMIT_NOTES[language][state.get("limit_hit")]
    summary = f"{note} {VERIFIED_ONLY[language]}" if points else NO_VERIFIED_ANSWER[language]
    return Report(
        language=language,
        status="partial",
        summary=summary,
        points=points,
        confidence=Confidence(level="low", note=note),
        unverified=unverified[:MAX_UNVERIFIED],
    )


async def finalize_partial_node(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
    return {"draft": partial_report(state)}
