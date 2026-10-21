"""Readable fact excerpts (tools.facts.render_fact)."""

from decimal import Decimal

import pytest

from agent.answer import figures
from tools.facts import format_number, metric_label, render_fact


@pytest.mark.parametrize(
    ("value", "language", "expected"),
    [
        (Decimal("1234.5"), "tr", "1.234,5"),
        (Decimal("1234.5"), "en", "1,234.5"),
        (Decimal("1000.00"), "tr", "1.000"),
        (Decimal("415696027"), "tr", "415.696.027"),
        (Decimal("0.25"), "en", "0.25"),
        (Decimal("-9876543.21"), "tr", "-9.876.543,21"),
        (Decimal("1E+3"), "en", "1,000"),
    ],
)
def test_format_number(value: Decimal, language: str, expected: str) -> None:
    assert format_number(value, language) == expected  # type: ignore[arg-type]


def test_render_fact_tr_and_en() -> None:
    args = ("net_income", "2025-Q3", Decimal("1234.5"), "million", "TRY")
    assert render_fact(*args, "tr") == "Net dönem kârı (2025-Q3): 1.234,5 milyon TRY"
    assert render_fact(*args, "en") == "Net income (2025-Q3): 1,234.5 million TRY"


def test_unknown_metric_and_unit_stay_readable() -> None:
    text = render_fact("free_cash_flow", "2024-FY", Decimal("12"), "units", None, "en")
    assert text == "free cash flow (2024-FY): 12 units"
    assert "_" not in metric_label("free_cash_flow", "tr")


@pytest.mark.parametrize("language", ["tr", "en"])
def test_rendered_value_is_found_by_the_validator(language: str) -> None:
    text = render_fact("revenue", "2025-Q3", Decimal("9876.5"), "million", "TRY", language)  # type: ignore[arg-type]
    assert Decimal("9876.5") in {f.value for f in figures(text, language)}  # type: ignore[arg-type]
