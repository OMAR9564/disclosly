"""Financial facts with a source reference (data-model.md `financial_facts`).

SEC: XBRL `companyfacts` concepts mapped to canonical metrics. Each fact links to the filing
document through its accession number. KAP: Turkish statement line labels from the tables of
a financial report, scaled by the report's presentation unit.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from ingestion.parse.types import Paragraph, ParsedDocument

# Concepts tried in order; the first concept that has a value for a (period, filing) wins.
SEC_CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ),
    "net_income": ("NetIncomeLoss",),
    "operating_income": ("OperatingIncomeLoss",),
    "total_debt": ("LongTermDebt", "LongTermDebtNoncurrent", "DebtInstrumentCarryingAmount"),
    "depreciation_amortization": (
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
    ),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "gross_profit": ("GrossProfit",),
    "cost_of_revenue": ("CostOfGoodsAndServicesSold", "CostOfRevenue"),
}
# Flow metrics are reported for a span; a quarter missing from the filings (the fiscal Q4, or
# a cash-flow line that is only given year to date) is the difference of two spans.
FLOW_METRICS = frozenset(
    {
        "revenue",
        "net_income",
        "operating_income",
        "depreciation_amortization",
        "gross_profit",
        "cost_of_revenue",
    }
)

_QUARTER_DAYS = range(80, 101)
_YEAR_DAYS = range(350, 381)


@dataclass(frozen=True)
class Span:
    """A flow figure for `start`..`end` (a quarter, half year, nine months or a year)."""

    metric: str
    start: date
    end: date
    value: Decimal
    unit: str
    currency: str | None
    source_document_id: int | None
    source_ref: str


@dataclass(frozen=True)
class FactDraft:
    metric: str
    period: str
    value: Decimal
    unit: str
    currency: str | None
    source_document_id: int
    source_ref: str
    derived: bool = False

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.metric, self.period, self.source_document_id)


def _sec_period(entry: Mapping[str, Any]) -> str | None:
    """FY for ~1-year durations or 10-K instants, calendar quarter for ~3-month ones."""
    end = date.fromisoformat(entry["end"])
    quarter = f"{end.year}-Q{(end.month - 1) // 3 + 1}"
    if "start" not in entry:
        return f"{end.year}-FY" if entry.get("form") == "10-K" else quarter
    days = (end - date.fromisoformat(entry["start"])).days
    if days in _YEAR_DAYS:
        return f"{end.year}-FY"
    if days in _QUARTER_DAYS:
        return quarter
    return None  # year-to-date and other spans are skipped


def extract_sec_facts(
    companyfacts: Mapping[str, Any], accession_to_document: Mapping[str, int]
) -> list[FactDraft]:
    """Canonical facts for the filings we stored; derives `ebitda` where both parts exist."""
    gaap: Mapping[str, Any] = companyfacts.get("facts", {}).get("us-gaap", {})
    facts: dict[tuple[str, str, int], FactDraft] = {}
    spans: dict[tuple[str, date, date], Span] = {}
    for metric, concepts in SEC_CONCEPTS.items():
        for concept in concepts:
            for entry in gaap.get(concept, {}).get("units", {}).get("USD", []):
                document_id = accession_to_document.get(entry.get("accn", ""))
                if metric in FLOW_METRICS and "start" in entry:
                    start, end = (
                        date.fromisoformat(entry["start"]),
                        date.fromisoformat(entry["end"]),
                    )
                    spans.setdefault(
                        (metric, start, end),
                        Span(
                            metric,
                            start,
                            end,
                            Decimal(str(entry["val"])),
                            "USD",
                            "USD",
                            document_id,
                            f"us-gaap:{concept} {entry.get('accn', '')}",
                        ),
                    )
                period = _sec_period(entry)
                if document_id is None or period is None:
                    continue
                draft = FactDraft(
                    metric=metric,
                    period=period,
                    value=Decimal(str(entry["val"])),
                    unit="USD",
                    currency="USD",
                    source_document_id=document_id,
                    source_ref=f"us-gaap:{concept} {entry['accn']}",
                )
                facts.setdefault(draft.key, draft)
    found = [*facts.values(), *derive_quarters(spans.values(), facts.values())]
    return [*found, *derive_ebitda(found)]


def _quarter(end: date) -> str:
    return f"{end.year}-Q{(end.month - 1) // 3 + 1}"


def span_period(start: date, end: date) -> str | None:
    days = (end - start).days
    if days in _YEAR_DAYS:
        return f"{end.year}-FY"
    if days in _QUARTER_DAYS:
        return _quarter(end)
    return None


def derive_quarters(spans: Iterable[Span], reported: Iterable[FactDraft]) -> list[FactDraft]:
    """Facts from flow spans that `reported` lacks: a quarter or a year as given, and a
    quarter the filings only give inside a longer span (Q4 = year - nine months, Q2 = half
    year - Q1): the longer span minus the span with the same start that ends a quarter earlier.

    A span without a stored document gives no fact; a derived fact cites the longer span's
    document, the earliest one first, so a report's own quarter wins over a later comparative."""
    have = {(f.metric, f.period) for f in reported}
    found: dict[tuple[str, str], FactDraft] = {}
    ordered = sorted(spans, key=lambda s: s.source_document_id or 0)
    for span in ordered:
        period = span_period(span.start, span.end)
        key = (span.metric, period or "")
        if period is None or span.source_document_id is None or key in have or key in found:
            continue
        found[key] = FactDraft(
            span.metric,
            period,
            span.value,
            span.unit,
            span.currency,
            span.source_document_id,
            span.source_ref,
        )
    by_start: dict[tuple[str, date], list[Span]] = {}
    for span in ordered:
        by_start.setdefault((span.metric, span.start), []).append(span)
    for group in by_start.values():
        for longer in group:
            if (
                longer.source_document_id is None
                or (longer.end - longer.start).days in _QUARTER_DAYS
            ):
                continue
            for shorter in group:
                if (
                    longer.end - shorter.end
                ).days not in _QUARTER_DAYS or shorter.unit != longer.unit:
                    continue
                key = (longer.metric, _quarter(longer.end))
                if key in have or key in found:
                    continue
                found[key] = FactDraft(
                    metric=longer.metric,
                    period=key[1],
                    value=longer.value - shorter.value,
                    unit=longer.unit,
                    currency=longer.currency,
                    source_document_id=longer.source_document_id,
                    source_ref=f"derived: {longer.source_ref} - {shorter.source_ref}",
                    derived=True,
                )
    return list(found.values())


def derive_ebitda(facts: Iterable[FactDraft]) -> list[FactDraft]:
    """EBITDA = operating income + depreciation and amortization, per period and document."""
    by_key = {f.key: f for f in facts}
    derived: list[FactDraft] = []
    for (metric, period, document_id), operating in by_key.items():
        if metric != "operating_income":
            continue
        da = by_key.get(("depreciation_amortization", period, document_id))
        if da is None or da.unit != operating.unit:
            continue
        derived.append(
            FactDraft(
                metric="ebitda",
                period=period,
                value=operating.value + da.value,
                unit=operating.unit,
                currency=operating.currency,
                source_document_id=document_id,
                source_ref=f"derived: {operating.source_ref} + {da.source_ref}",
                derived=True,
            )
        )
    return derived


# KAP: Turkish statement line labels (as rendered by KAP, case-insensitive) to metrics.
KAP_LABELS: dict[str, str] = {
    "hasılat": "revenue",
    "dönem karı (zararı)": "net_income",
    "esas faaliyet karı (zararı)": "operating_income",
    "amortisman ve itfa gideri ile ilgili düzeltmeler": "depreciation_amortization",
    "nakit ve nakit benzerleri": "cash",
    "finansal borçlar": "total_debt",
    "brüt kar (zarar)": "gross_profit",
    "satışların maliyeti": "cost_of_revenue",
}
# Summed into `total_debt` when the report has no single "Finansal Borçlar" line.
KAP_DEBT_LABELS = (
    "kısa vadeli borçlanmalar",
    "uzun vadeli borçlanmaların kısa vadeli kısımları",
    "uzun vadeli borçlanmalar",
)
_KAP_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_UNIT_RE = re.compile(r"Sunum Para Birimi:\s*([\d.]+)?\s*(TL|USD|EUR)", re.IGNORECASE)
_CURRENCIES = {"TL": "TRY", "USD": "USD", "EUR": "EUR"}


def _tr_lower(text: str) -> str:
    return " ".join(text.replace("İ", "i").replace("I", "ı").lower().split())


def _kap_period(header: str) -> str | None:
    """Column header to period: instants and ~quarter/~year spans; year-to-date is skipped."""
    dates = [date(int(y), int(m), int(d)) for d, m, y in _KAP_DATE_RE.findall(header)]
    if len(dates) == 1:
        end = dates[0]
        if (end.month, end.day) == (12, 31):
            return f"{end.year}-FY"
        return f"{end.year}-Q{(end.month - 1) // 3 + 1}"
    if len(dates) == 2:
        days = (dates[1] - dates[0]).days
        if days in _YEAR_DAYS:
            return f"{dates[1].year}-FY"
        if days in _QUARTER_DAYS:
            return f"{dates[1].year}-Q{(dates[1].month - 1) // 3 + 1}"
    return None


def kap_unit(paragraphs: Iterable[str]) -> tuple[Decimal, str]:
    """The presentation unit ("Sunum Para Birimi: 1.000 TL") of a report's text."""
    for text in paragraphs:
        match = _UNIT_RE.search(text)
        if match is not None:
            scale = Decimal(match.group(1).replace(".", "")) if match.group(1) else Decimal(1)
            return scale, _CURRENCIES[match.group(2).upper()]
    return Decimal(1), "TRY"


def extract_kap_facts(parsed: ParsedDocument, document_id: int) -> list[FactDraft]:
    """Canonical facts from KAP financial statement tables (label, concept, note, values...)."""
    facts: dict[tuple[str, str, int], FactDraft] = {}
    debt_parts: dict[str, list[FactDraft]] = {}
    for section in parsed.sections:
        scale, currency = kap_unit(b.text for b in section.blocks if isinstance(b, Paragraph))
        where = f"{section.label} ({section.title})" if section.title else section.label
        for table in section.tables:
            for row in table.rows:
                label = _tr_lower(row[0])
                metric = KAP_LABELS.get(label)
                is_debt_part = label in KAP_DEBT_LABELS
                if metric is None and not is_debt_part:
                    continue
                for column in range(3, min(len(row), len(table.header))):
                    period = _kap_period(table.header[column])
                    if period is None or not row[column]:
                        continue
                    try:
                        value = Decimal(row[column]) * scale
                    except ArithmeticError:
                        continue
                    draft = FactDraft(
                        metric=metric or "total_debt",
                        period=period,
                        value=value,
                        unit=currency,
                        currency=currency,
                        source_document_id=document_id,
                        source_ref=f"{where} / {row[0]} / {table.header[column]}",
                    )
                    if is_debt_part:
                        debt_parts.setdefault(period, []).append(draft)
                    else:
                        facts.setdefault(draft.key, draft)
    for period, parts in debt_parts.items():
        key = ("total_debt", period, document_id)
        if key in facts:
            continue
        facts[key] = FactDraft(
            metric="total_debt",
            period=period,
            value=sum((p.value for p in parts), Decimal(0)),
            unit=parts[0].unit,
            currency=parts[0].currency,
            source_document_id=document_id,
            source_ref="derived: " + " + ".join(p.source_ref for p in parts),
            derived=True,
        )
    return [*facts.values(), *derive_ebitda(facts.values())]


def _kap_dates(header: str) -> tuple[date, date] | None:
    dates = [date(int(y), int(m), int(d)) for d, m, y in _KAP_DATE_RE.findall(header)]
    return (dates[0], dates[1]) if len(dates) == 2 else None


def kap_spans(
    header: list[str], rows: Iterable[list[str]], scale: Decimal, currency: str, document_id: int
) -> list[Span]:
    """Flow figures of a stored KAP statement table, year-to-date columns included."""
    spans = []
    for row in rows:
        metric = KAP_LABELS.get(_tr_lower(row[0]))
        if metric not in FLOW_METRICS:
            continue
        for column in range(3, min(len(row), len(header))):
            dates = _kap_dates(header[column])
            if dates is None or not row[column]:
                continue
            try:
                value = Decimal(row[column]) * scale
            except ArithmeticError:
                continue
            spans.append(
                Span(
                    metric,
                    dates[0],
                    dates[1],
                    value,
                    currency,
                    currency,
                    document_id,
                    f"{row[0]} / {header[column]}",
                )
            )
    return spans
