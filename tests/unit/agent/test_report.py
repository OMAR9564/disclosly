"""agent.report (T008): the Report schema and its code-checked rules."""

import json
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from agent.answer import DISCLAIMER, Answer
from agent.report import (
    Report,
    cell_number,
    finalize_report,
    report_json_schema,
    to_partial,
    validate_report,
)
from tools.types import SeenItem

DEBT = SeenItem(
    cite_id="f:88",
    text="Net borç (2025-Q4): 41,2 milyar TRY",
    value=Decimal("41.2"),
    scale=Decimal(10) ** 9,
    source={"kind": "fact"},
)
EBITDA = SeenItem(
    cite_id="f:91",
    text="FAVÖK (2025-Q4): 21,7 milyar TRY",
    value=Decimal("21.7"),
    scale=Decimal(10) ** 9,
    source={"kind": "fact"},
)
DEBT_Q3 = SeenItem(
    cite_id="f:87",
    text="Net borç (2025-Q3): 38,5 milyar TRY",
    value=Decimal("38.5"),
    scale=Decimal(10) ** 9,
    source={"kind": "fact"},
)
SEEN = {item.cite_id: item for item in (DEBT, EBITDA, DEBT_Q3)}
CALCS = {"k:1": Decimal("1.898617511520737327188940092")}


def _report(**fields: Any) -> Report:
    body: dict[str, Any] = {
        "language": "tr",
        "status": "completed",
        "summary": "Net borç / FAVÖK 2025-Q4'te 1,9 oldu [1].",
        "points": [{"text": "Net borç 41,2 milyar TL", "cite_ids": ["f:88"]}],
        "tables": [
            {
                "title": "Net borç",
                "columns": ["Dönem", "Net borç", "Net borç / FAVÖK"],
                "rows": [
                    [
                        {"text": "2025-Q3"},
                        {"text": "38,5 milyar TL", "cite_id": "f:87"},
                        {"text": "—"},
                    ],
                    [
                        {"text": "2025-Q4"},
                        {"text": "41,2 milyar TL", "cite_id": "f:88"},
                        {"text": "1,9", "cite_id": "k:1"},
                    ],
                ],
            }
        ],
        "chart": {"kind": "line", "table": 0, "x_column": "Dönem", "series": ["Net borç"]},
        "confidence": {"level": "high", "note": "Raporlanan rakamlar."},
        "citations": [{"marker": 1, "cite_id": "k:1"}],
    }
    body.update(fields)
    return Report.model_validate(body)


def test_valid_report_passes() -> None:
    result = validate_report(_report(), SEEN, CALCS, "tr")
    assert result.errors == []
    assert result.ok


def test_phase3_answer_json_loads_as_report() -> None:
    answer = Answer(
        language="en",
        status="completed",
        summary="Net income was 1,234.5 million [1].",
        points=[{"text": "Net income 1,234.5 million", "cite_ids": ["f:1"]}],
        citations=[{"marker": 1, "cite_id": "f:1"}],
    )
    stored = json.loads(answer.model_dump_json())
    report = Report.model_validate(stored)
    assert report.tables == []
    assert report.chart is None
    assert report.confidence is None
    assert report.unverified == []
    assert Report.from_answer(answer) == report


def test_disclaimer_is_server_set() -> None:
    assert finalize_report(_report(disclaimer="buy now")).disclaimer == DISCLAIMER


@pytest.mark.parametrize(
    ("change", "error"),
    [
        # Rule 3: a numeric cell without a citation.
        (
            lambda r: r["tables"][0]["rows"][1][1].pop("cite_id"),
            "a number needs a cite_id",
        ),
        # Rule 2: the cited fact does not contain the number.
        (
            lambda r: r["tables"][0]["rows"][1][1].update(text="42,0 milyar TL"),
            "numbers not found in the cited source",
        ),
        # Rule 2: a calculation output shown at the wrong value.
        (
            lambda r: r["tables"][0]["rows"][1][2].update(text="2,4"),
            "numbers not found in the cited source",
        ),
        # Rule 4: chart column missing from the table.
        (lambda r: r["chart"].update(series=["FAVÖK"]), "chart columns not in table"),
        # Rule 4: chart table index out of range.
        (lambda r: r["chart"].update(table=3), "chart table 3 does not exist"),
        # Rule 4: a non-numeric series.
        (
            lambda r: r["chart"].update(series=["Net borç / FAVÖK"]),
            "has a non-numeric cell",
        ),
        # Rule 1: unknown cite id.
        (lambda r: r["points"][0].update(cite_ids=["f:999"]), "unknown cite_id"),
        # Rule 1: language mismatch.
        (lambda r: r.update(language="en"), "language must be 'tr'"),
        # Rule 5: partial without low confidence and an unverified list.
        (lambda r: r.update(status="partial"), "a partial report needs confidence low"),
        # Rule 6: refused with a table.
        (lambda r: r.update(status="refused"), "a refused report has no tables"),
        # Summary number not in its cited source.
        (lambda r: r.update(summary="Oran 2,5 oldu [1]."), "summary numbers not found"),
        (lambda r: r.update(summary="Oran yükseldi [3]."), "summary markers without a citation"),
        (lambda r: r["points"][0].update(cite_ids=[]), "point 0 has no cite_ids"),
    ],
)
def test_rule_violations(change: Any, error: str) -> None:
    body = _report().model_dump()
    change(body)
    result = validate_report(Report.model_validate(body), SEEN, CALCS, "tr")
    assert not result.ok
    assert any(error in e for e in result.errors), result.errors


def test_calc_output_matches_at_displayed_precision() -> None:
    for text, ok in (("1,9", True), ("1,90", True), ("1,899", True), ("1,8", False), ("2", True)):
        body = _report().model_dump()
        body["tables"][0]["rows"][1][2]["text"] = text
        result = validate_report(Report.model_validate(body), SEEN, CALCS, "tr")
        assert result.ok is ok, (text, result.errors)


def test_calc_ratio_shown_as_percent() -> None:
    calcs = {"k:2": Decimal("0.1234")}
    report = _report(
        points=[{"text": "Marj %12,3 oldu", "cite_ids": ["k:2"]}],
        citations=[{"marker": 1, "cite_id": "k:1"}],
    )
    assert validate_report(report, SEEN, {**CALCS, **calcs}, "tr").ok


def test_unverified_calculation_is_unknown() -> None:
    # Only verified calculations are passed in; an unverified k: id is an unknown citation.
    result = validate_report(_report(), SEEN, {}, "tr")
    assert any("unknown cite_id" in e for e in result.errors)


def test_partial_allowed_when_limit_hit() -> None:
    report = _report(status="partial")
    assert validate_report(report, SEEN, CALCS, "tr", limit_hit=True).ok
    low = _report(status="partial", confidence={"level": "low", "note": ""}, unverified=["x"])
    assert validate_report(low, SEEN, CALCS, "tr").ok


def test_no_evidence_without_tables_is_fine() -> None:
    report = _report(
        status="no_evidence",
        summary="Kaynak bulunamadı.",
        points=[],
        tables=[],
        chart=None,
        citations=[],
    )
    assert validate_report(report, SEEN, CALCS, "tr").ok


@pytest.mark.parametrize(
    "body",
    [
        {"tables": [{"title": "t", "columns": [f"c{i}" for i in range(9)], "rows": []}]},
        {"tables": [{"title": "t", "columns": ["a"], "rows": [[{"text": "1"}]] * 25}]},
        {"tables": [{"title": "t", "columns": ["a", "b"], "rows": [[{"text": "1"}]]}]},
        {"tables": [{"title": "t", "columns": ["a"], "rows": []}] * 5},
        {"chart": {"kind": "pie", "table": 0, "x_column": "a", "series": ["b"]}},
        {"chart": {"kind": "bar", "table": 0, "x_column": "a", "series": ["b"] * 5}},
        {"confidence": {"level": "low", "note": "x" * 401}},
        {"unverified": ["x"] * 21},
        {"extra": "field"},
    ],
)
def test_schema_limits(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Report.model_validate({"language": "en", "status": "completed", "summary": "", **body})


def test_json_schema_describes_tables_and_chart() -> None:
    schema = report_json_schema()
    assert {"tables", "chart", "confidence", "unverified"} <= set(schema["properties"])


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("41,2 milyar TL", Decimal("41.2E9")),
        ("1,9", Decimal("1.9")),
        ("%12,5", Decimal("12.5")),
        ("—", None),
        ("2025-Q4", None),
        ("1,9 ve 2,1", None),
    ],
)
def test_cell_number(text: str, value: Decimal | None) -> None:
    assert cell_number(text, "tr") == value


def test_to_partial_drops_failing_points_rows_and_chart() -> None:
    body = _report().model_dump()
    body["points"].append({"text": "Net borç 99 milyar TL", "cite_ids": ["f:88"]})
    body["tables"][0]["rows"][0][1]["text"] = "39,0 milyar TL"  # wrong Q3 value
    report = Report.model_validate(body)
    validation = validate_report(report, SEEN, CALCS, "tr")
    assert validation.bad_points == {1}
    assert validation.bad_rows == {(0, 0)}

    partial = to_partial(report, validation, "tr")

    assert partial.status == "partial"
    assert partial.confidence is not None
    assert partial.confidence.level == "low"
    assert [p.text for p in partial.points] == ["Net borç 41,2 milyar TL"]
    assert len(partial.tables[0].rows) == 1
    assert partial.chart is None  # its table lost a row
    assert "Net borç 99 milyar TL" in partial.unverified
    assert any("39,0 milyar TL" in u for u in partial.unverified)
    assert validate_report(partial, SEEN, CALCS, "tr").ok


def test_to_partial_keeps_chart_of_untouched_table() -> None:
    body = _report().model_dump()
    body["points"][0]["text"] = "Net borç 50 milyar TL"
    report = Report.model_validate(body)
    partial = to_partial(report, validate_report(report, SEEN, CALCS, "tr"), "tr")
    assert partial.chart is not None
    assert partial.points == []
    assert partial.unverified == ["Net borç 50 milyar TL"]


def test_to_partial_drops_the_chart_of_an_emptied_table() -> None:
    report = _report()
    validation = validate_report(report, SEEN, CALCS, "tr")
    assert report.chart is not None
    table = report.chart.table
    validation.bad_rows = {(table, r) for r in range(len(report.tables[table].rows))}

    partial = to_partial(report, validation, "tr")

    assert len(partial.tables) == len(report.tables) - 1
    assert partial.chart is None


def test_to_partial_with_nothing_left() -> None:
    report = _report(
        summary="Oran 7 [1].",
        points=[{"text": "x 5", "cite_ids": ["f:88"]}],
        tables=[],
        chart=None,
    )
    partial = to_partial(report, validate_report(report, SEEN, CALCS, "en"), "en")
    assert partial.summary.startswith("No answer could be verified")
    assert partial.citations == []
    assert partial.unverified[0] == "Oran 7 [1]."
