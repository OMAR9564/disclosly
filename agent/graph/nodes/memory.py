"""memory: run the watchlist change the user's own question asks for (research §R13).

`router.memory_intent` comes from the user's question alone (the router never sees retrieved
text) and names a ticker the question itself contains. This node makes no model call: it
applies the change through `agent/memory.py` (corpus companies only, at most 50, idempotent)
and emits `memory_updated` with the action and ticker. A refused change is not saved; the
report says why. `memory_note` gives the sentence every report adds when the run used or
changed memory (FR-030, FR-031); it holds no digits, so it never adds figures to check.
"""

from typing import Any

from agent.answer import Language
from agent.graph.context import NodeContext
from agent.graph.state import PeriodRange, ResearchState
from agent.report import Report

RANGE_LABELS: dict[Language, dict[PeriodRange, str]] = {
    "tr": {
        "last_4q": "son dört çeyrek",
        "last_8q": "son sekiz çeyrek",
        "last_2fy": "son iki mali yıl",
        "last_3fy": "son üç mali yıl",
    },
    "en": {
        "last_4q": "the last four quarters",
        "last_8q": "the last eight quarters",
        "last_2fy": "the last two fiscal years",
        "last_3fy": "the last three fiscal years",
    },
}
NOTES: dict[Language, dict[str, str]] = {
    "tr": {
        "watchlist": "Takip listenizdeki şirketler kullanıldı: {tickers}.",
        "left_out": "Plan sınırı nedeniyle şu şirketler kapsanmadı: {tickers}.",
        "range": "Soruda dönem belirtilmediği için varsayılan dönem aralığınız ({label}) "
        "kullanıldı.",
        "added": "{ticker} takip listenize eklendi.",
        "removed": "{ticker} takip listenizden çıkarıldı.",
        "unchanged_add": "{ticker} zaten takip listenizdeydi.",
        "unchanged_remove": "{ticker} takip listenizde değildi.",
        "company_not_covered": "{ticker} Disclosly kapsamında olmadığı için takip listesine "
        "eklenmedi.",
        "watchlist_full": "Takip listeniz dolu olduğu için {ticker} eklenmedi.",
        "empty": "Takip listeniz boş. Ayarlar sayfasından ya da soru sorarak şirket "
        "ekleyebilirsiniz.",
    },
    "en": {
        "watchlist": "Companies from your watchlist were used: {tickers}.",
        "left_out": "These companies were left out because of the plan limit: {tickers}.",
        "range": "The question named no period, so your default range ({label}) was used.",
        "added": "{ticker} was added to your watchlist.",
        "removed": "{ticker} was removed from your watchlist.",
        "unchanged_add": "{ticker} was already on your watchlist.",
        "unchanged_remove": "{ticker} was not on your watchlist.",
        "company_not_covered": "{ticker} is not in Disclosly's coverage, so it was not added.",
        "watchlist_full": "Your watchlist is full, so {ticker} was not added.",
        "empty": "Your watchlist is empty. Add companies on the settings page or by asking.",
    },
}


def watchlist_empty(state: ResearchState) -> bool:
    router = state.get("router")
    return bool(state.get("used_watchlist")) and router is not None and not router.tickers


def memory_note(state: ResearchState, language: Language) -> str:
    notes = NOTES[language]
    parts: list[str] = []
    router = state.get("router")
    intent = router.memory_intent if router is not None else None
    result = state.get("memory_result")
    if intent is not None and result is not None:
        key = result
        if result == "unchanged":
            key = "unchanged_add" if intent.action == "watchlist_add" else "unchanged_remove"
        if key in notes:
            parts.append(notes[key].format(ticker=intent.ticker))
    if state.get("used_watchlist") and router is not None and router.tickers:
        parts.append(notes["watchlist"].format(tickers=", ".join(router.tickers)))
        if left_out := state.get("watchlist_left_out"):
            parts.append(notes["left_out"].format(tickers=", ".join(left_out)))
    if period_range := state.get("used_period_range"):
        parts.append(notes["range"].format(label=RANGE_LABELS[language][period_range]))
    return " ".join(parts)


def with_memory_note(report: Report, state: ResearchState) -> Report:
    """The report with the memory sentence appended to its summary."""
    note = memory_note(state, report.language)
    if not note or report.status == "refused":
        return report
    summary = f"{report.summary} {note}".strip() if report.summary else note
    return report.model_copy(update={"summary": summary})


async def memory_node(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
    router = state.get("router")
    intent = router.memory_intent if router is not None else None
    store = ctx.deps.memory
    if intent is None or store is None or not state.get("user_id"):
        return {}
    result = await store.apply(state["user_id"], intent)
    if result in ("added", "removed", "unchanged"):
        await ctx.emit("memory_updated", {"action": intent.action, "ticker": intent.ticker})
    return {"memory_result": result}
