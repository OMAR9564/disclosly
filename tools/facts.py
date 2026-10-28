"""`get_financial_facts` over the `financial_facts` table (contracts/agent-answer.md)."""

import asyncio
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Engine, select

from ingestion import schema
from ingestion.models import Period
from tools.types import SeenItem

MAX_METRICS = 10
MAX_PERIODS = 12

Language = Literal["tr", "en"]

METRIC_LABELS: dict[str, dict[Language, str]] = {
    "net_income": {"tr": "Net dönem kârı", "en": "Net income"},
    "cash": {"tr": "Nakit ve nakit benzerleri", "en": "Cash and cash equivalents"},
    "total_debt": {"tr": "Toplam borç", "en": "Total debt"},
    "depreciation_amortization": {"tr": "Amortisman", "en": "Depreciation and amortization"},
    "revenue": {"tr": "Hasılat", "en": "Revenue"},
    "gross_profit": {"tr": "Brüt kâr", "en": "Gross profit"},
    "cost_of_revenue": {"tr": "Satışların maliyeti", "en": "Cost of revenue"},
    "operating_income": {"tr": "Esas faaliyet kârı", "en": "Operating income"},
    "ebitda": {"tr": "FAVÖK", "en": "EBITDA"},
    "net_debt": {"tr": "Net borç", "en": "Net debt"},
    "total_assets": {"tr": "Toplam varlıklar", "en": "Total assets"},
    "total_equity": {"tr": "Özkaynaklar", "en": "Total equity"},
    "operating_cash_flow": {"tr": "İşletme nakit akışı", "en": "Operating cash flow"},
    "eps": {"tr": "Hisse başına kâr", "en": "Earnings per share"},
}
# Metrics the ingestion pipeline extracts into `financial_facts` (ingestion/facts).
KNOWN_METRICS = (
    "net_income",
    "revenue",
    "operating_income",
    "ebitda",
    "cash",
    "total_debt",
    "depreciation_amortization",
    "gross_profit",
    "cost_of_revenue",
)

UNIT_SCALES: dict[str, Decimal] = {
    "thousand": Decimal(10) ** 3,
    "million": Decimal(10) ** 6,
    "billion": Decimal(10) ** 9,
}
UNIT_LABELS: dict[str, dict[Language, str]] = {
    "thousand": {"tr": "bin", "en": "thousand"},
    "million": {"tr": "milyon", "en": "million"},
    "billion": {"tr": "milyar", "en": "billion"},
}


def metric_label(metric: str, language: Language) -> str:
    return METRIC_LABELS.get(metric, {}).get(language) or metric.replace("_", " ")


def format_number(value: Decimal, language: Language) -> str:
    """`1.234,5` (tr) or `1,234.5` (en); trailing zeros dropped."""
    text = format(value.normalize(), "f")
    whole, _, fraction = text.partition(".")
    sign = "-" if whole.startswith("-") else ""
    grouped = f"{int(whole.lstrip('-')):,}"
    if language == "tr":
        grouped = grouped.replace(",", ".")
    decimal = "," if language == "tr" else "."
    return sign + grouped + (decimal + fraction if fraction else "")


REPORT_LABEL: dict[Language, str] = {"tr": "{period} raporu", "en": "{period} report"}
# Balance-sheet amounts are stocks at a date: the year-end balance sheet (stored as YYYY-FY,
# since KAP and SEC file one annual report instead of a Q4 one) is also the YYYY-Q4 figure.
# Flow amounts (revenue, income, EBITDA) are not: a year is not its fourth quarter.
BALANCE_SHEET_METRICS = frozenset(
    {"cash", "total_debt", "net_debt", "total_assets", "total_equity"}
)
YEAR_END_LABEL: dict[Language, str] = {
    "tr": "{period} yıl sonu bilançosu",
    "en": "{period} year-end balance sheet",
}


def source_column(source_ref: str | None) -> str | None:
    """The KAP table column of a fact, e.g. "Önceki Dönem 3 Aylık 01.04.2025 - 30.06.2025"."""
    if not source_ref or " / " not in source_ref:
        return None  # SEC refs are concept names, not table columns
    return source_ref.rsplit(" / ", 1)[1].strip() or None


def render_fact(
    metric: str,
    period: str,
    value: Decimal,
    unit: str,
    currency: str | None,
    language: Language,
    *,
    column: str | None = None,
    report_period: str | None = None,
) -> str:
    """Readable citation excerpt, e.g. "Net dönem kârı (2025-Q3): 1.234,5 milyon TRY".

    A figure taken from a later report's prior-period column names that column and report, so
    a restated amount (e.g. inflation-adjusted under IAS 29) is not mistaken for the original.
    """
    unit_text = UNIT_LABELS.get(unit, {}).get(language, unit)
    # A currency unit ("TRY") is not repeated as the currency.
    tail = " ".join(p for p in dict.fromkeys((unit_text, currency)) if p)
    number = format_number(value, language)
    label = [period]
    if column:
        label.append(column)
    if report_period and report_period != period:
        label.append(REPORT_LABEL[language].format(period=report_period))
    head = f"{metric_label(metric, language)} ({'; '.join(label)})"
    return f"{head}: {number}" + (f" {tail}" if tail else "")


def _query(
    engine: Engine, ticker: str, metrics: list[str], periods: list[str], language: Language
) -> list[SeenItem]:
    facts, companies, docs = schema.financial_facts, schema.companies, schema.documents
    # YYYY-Q4 asked: the year-end balance sheet (YYYY-FY) can answer its balance-sheet metrics.
    year_ends = {f"{p[:4]}-FY": p for p in periods if p.endswith("-Q4")}
    wanted = list(dict.fromkeys([*periods, *year_ends]))
    query = (
        select(
            facts.c.id,
            facts.c.metric,
            facts.c.period,
            facts.c.value,
            facts.c.unit,
            facts.c.currency,
            facts.c.source_document_id,
            facts.c.source_ref,
            companies.c.ticker,
            docs.c.doc_type,
            docs.c.source_url,
            docs.c.period.label("report_period"),
        )
        .select_from(
            facts.join(companies, companies.c.id == facts.c.company_id).join(
                docs, docs.c.id == facts.c.source_document_id
            )
        )
        .where(
            companies.c.ticker == ticker,
            facts.c.metric.in_(metrics),
            facts.c.period.in_(wanted),
        )
        # The figure as first reported comes before restatements in later reports.
        .order_by(facts.c.metric, facts.c.period, docs.c.period != facts.c.period, facts.c.id)
    )
    with engine.connect() as conn:
        found = list(conn.execute(query))
    asked = set(periods)
    have = {(r.metric, r.period) for r in found if r.period in asked}
    rows: list[tuple[Any, str, str | None]] = []  # (row, period shown, year-end period)
    for r in found:
        if r.period in asked:
            rows.append((r, r.period, None))
        elif (
            r.metric in BALANCE_SHEET_METRICS
            and (quarter := year_ends.get(r.period))
            and (r.metric, quarter) not in have
        ):
            have.add((r.metric, quarter))
            rows.append((r, quarter, r.period))
    return [
        SeenItem(
            cite_id=f"f:{r.id}",
            text=render_fact(
                r.metric,
                period,
                r.value,
                r.unit,
                r.currency,
                language,
                column=YEAR_END_LABEL[language].format(period=year_end)
                if year_end
                else source_column(r.source_ref),
                report_period=r.report_period,
            ),
            value=r.value,
            scale=UNIT_SCALES.get(r.unit, Decimal(1)),
            source={
                "kind": "fact",
                "document_id": r.source_document_id,
                "ticker": r.ticker,
                "doc_type": r.doc_type,
                "period": period,
                **({"year_end": year_end} if year_end else {}),
                "metric": r.metric,
                "unit": r.unit,
                "currency": r.currency,
                "source_ref": r.source_ref,
                "source_url": r.source_url,
            },
        )
        for r, period, year_end in rows
    ]


async def get_financial_facts(
    engine: Engine,
    *,
    ticker: str,
    metrics: list[str],
    periods: list[str],
    language: Language = "tr",
) -> list[SeenItem]:
    if not metrics or len(metrics) > MAX_METRICS:
        raise ValueError(f"metrics must have 1 to {MAX_METRICS} entries")
    if not periods or len(periods) > MAX_PERIODS:
        raise ValueError(f"periods must have 1 to {MAX_PERIODS} entries")
    for period in periods:
        Period.parse(period)
    return await asyncio.to_thread(
        _query, engine, ticker.strip().upper(), [m.strip() for m in metrics], periods, language
    )
