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
}

_QUARTER_DAYS = range(80, 101)
_YEAR_DAYS = range(350, 381)


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
    for metric, concepts in SEC_CONCEPTS.items():
        for concept in concepts:
            for entry in gaap.get(concept, {}).get("units", {}).get("USD", []):
                document_id = accession_to_document.get(entry.get("accn", ""))
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
    return [*facts.values(), *derive_ebitda(facts.values())]


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


def _kap_unit(paragraphs: Iterable[str]) -> tuple[Decimal, str]:
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
        scale, currency = _kap_unit(b.text for b in section.blocks if isinstance(b, Paragraph))
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
