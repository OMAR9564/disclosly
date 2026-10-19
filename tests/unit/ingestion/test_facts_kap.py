from decimal import Decimal
from pathlib import Path

from ingestion.facts import FactDraft, _kap_period, _kap_unit, extract_kap_facts
from ingestion.parse.kap import parse_kap_detail
from ingestion.parse.types import ExtractedTable, Paragraph, ParsedDocument, ParsedSection

FIXTURE = Path(__file__).parents[2] / "fixtures" / "kap" / "detail_financial_report.json"
MILLION = Decimal(1_000_000)


def _facts() -> dict[tuple[str, str], FactDraft]:
    parsed = parse_kap_detail(FIXTURE.read_bytes())
    return {(f.metric, f.period): f for f in extract_kap_facts(parsed, 42)}


def test_maps_turkish_labels_scaled_to_try() -> None:
    facts = _facts()
    revenue_q3 = facts[("revenue", "2025-Q3")]
    assert revenue_q3.value == Decimal(282789) * MILLION  # the 3-month column
    assert (revenue_q3.unit, revenue_q3.currency) == ("TRY", "TRY")
    assert revenue_q3.source_document_id == 42
    assert revenue_q3.source_ref == (
        "Tablo 2 (Kar veya Zarar ve Diğer Kapsamlı Gelir Tablosu) / Hasılat / "
        "Cari Dönem 3 Aylık 01.07.2025 - 30.09.2025"
    )
    assert facts[("revenue", "2024-Q3")].value == Decimal(221815) * MILLION
    assert facts[("operating_income", "2025-Q3")].value == Decimal(43078) * MILLION
    assert facts[("net_income", "2025-Q3")].value == Decimal(56016) * MILLION
    assert facts[("cash", "2025-Q3")].value == Decimal(100325) * MILLION  # instant
    assert facts[("cash", "2024-FY")].value == Decimal(95992) * MILLION


def test_year_to_date_columns_are_skipped() -> None:
    facts = _facts()
    # Cash flow only has 9-month columns, so no quarterly D&A and no quarterly EBITDA.
    assert not any(metric == "depreciation_amortization" for metric, _ in facts)
    assert ("ebitda", "2025-Q3") not in facts


def test_total_debt_summed_from_borrowing_lines() -> None:
    debt = _facts()[("total_debt", "2025-Q3")]
    assert debt.derived is True
    assert debt.value == Decimal(61010 + 493725) * MILLION
    assert debt.source_ref.startswith("derived: ")


def _statement(rows: list[list[str]], header: list[str], unit: str = "1 TL") -> ParsedDocument:
    table = ExtractedTable(header=["Kalem", "Kavram", "Dipnot", *header], rows=rows)
    section = ParsedSection("Tablo 1", blocks=[Paragraph(f"Sunum Para Birimi: {unit}"), table])
    return ParsedDocument([section])


def test_annual_report_derives_ebitda_and_uses_single_debt_line() -> None:
    year = ["Cari Dönem 01.01.2025 - 31.12.2025"]
    parsed = _statement(
        [
            ["ESAS FAALİYET KARI (ZARARI)", "c", "", "100"],
            ["Amortisman ve İtfa Gideri İle İlgili Düzeltmeler", "c", "", "20"],
            ["Finansal Borçlar", "c", "", "50"],
            ["Kısa Vadeli Borçlanmalar", "c", "", "5"],
            ["Hasılat", "c", "", ""],
            ["Hasılat", "c", "", "n/a"],
        ],
        year,
    )
    facts = {(f.metric, f.period): f for f in extract_kap_facts(parsed, 1)}
    assert facts[("ebitda", "2025-FY")].value == Decimal(120)
    assert facts[("total_debt", "2025-FY")].value == Decimal(50)
    assert facts[("total_debt", "2025-FY")].derived is False
    assert ("revenue", "2025-FY") not in facts


def test_period_and_unit_helpers() -> None:
    assert _kap_period("Cari Dönem 31.12.2025") == "2025-FY"
    assert _kap_period("Cari Dönem 30.06.2025") == "2025-Q2"
    assert _kap_period("Cari Dönem 01.01.2025 - 30.06.2025") is None
    assert _kap_period("") is None
    assert _kap_unit(["Sunum Para Birimi: 1.000 TL"]) == (Decimal(1000), "TRY")
    assert _kap_unit(["Sunum Para Birimi: USD"]) == (Decimal(1), "USD")
    assert _kap_unit([]) == (Decimal(1), "TRY")
