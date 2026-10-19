import pytest

from ingestion.models import DocType, DocumentStatus, Language, Period


def test_enum_values_match_data_model() -> None:
    assert {d.value for d in DocType} == {
        "10-K",
        "10-Q",
        "kap_financial_report",
        "kap_material_event",
        "kap_annual_report",
        "kap_prospectus",
        "kap_fund_report",
    }
    assert {lang.value for lang in Language} == {"tr", "en"}
    assert [s.value for s in DocumentStatus] == [
        "fetched",
        "parsed",
        "chunked",
        "embedded",
        "failed",
    ]


@pytest.mark.parametrize(
    ("raw", "year", "quarter"),
    [("2025-Q3", 2025, 3), ("2025-FY", 2025, None), ("2024-Q1", 2024, 1)],
)
def test_period_parse(raw: str, year: int, quarter: int | None) -> None:
    period = Period.parse(raw)
    assert (period.year, period.quarter) == (year, quarter)
    assert str(period) == raw


@pytest.mark.parametrize("raw", ["2025", "2025-Q5", "2025-Q0", "25-Q1", "2025-fy", "x", ""])
def test_period_parse_rejects_bad_input(raw: str) -> None:
    with pytest.raises(ValueError, match="period"):
        Period.parse(raw)


def test_period_ordering() -> None:
    ordered = ["2024-Q1", "2024-Q4", "2024-FY", "2025-Q1", "2025-Q3", "2025-FY"]
    shuffled = ["2025-FY", "2024-Q4", "2025-Q1", "2024-FY", "2024-Q1", "2025-Q3"]
    assert [str(p) for p in sorted(Period.parse(s) for s in shuffled)] == ordered
    assert Period.parse("2024-Q4") < Period.parse("2024-FY") < Period.parse("2025-Q1")


def test_period_in_range() -> None:
    start, end = Period.parse("2024-Q3"), Period.parse("2025-Q1")
    assert Period.parse("2024-FY").within(start, end)
    assert not Period.parse("2025-Q2").within(start, end)
    assert Period.parse("2024-Q3").within(start, end)
