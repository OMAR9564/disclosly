"""The `Report` schema and its checks in code (specs/004-multi-agent-graph/contracts/report.md).

`Report` is a superset of the Phase 3 `Answer`: an `Answer` JSON loads as a `Report` with the
new fields at their defaults, so old run rows stay readable. Rules checked after the schema:

1. Phase 3 rules: every `cite_id` was produced in this run (by a tool or as a calculation),
   the disclaimer is server-set, `language` matches the question;
2. every number in `summary`, `points` and table cells is in a cited fact or chunk or equals
   the output of a verified calculation it cites, at the precision shown;
3. a table cell containing a number has a `cite_id`;
4. the chart names an existing table, `x_column` and every series are its columns, and every
   series cell is numeric (plotted values are read from those cells);
5. `status = partial` needs `confidence.level = low` and a non-empty `unverified`, unless a
   limit was hit;
6. `refused` and `no_evidence` reports have no tables and no chart.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.answer import (
    DISCLAIMER,
    NO_VERIFIED_ANSWER,
    Answer,
    AnswerStatus,
    CitationRef,
    Language,
    Point,
    figures,
    unsupported_figures,
)
from tools.types import SeenItem

MAX_TABLES = 4
MAX_COLUMNS = 8
MAX_ROWS = 24
MAX_SERIES = 4
MAX_UNVERIFIED = 20
NOTE_MAX_CHARS = 400
CELL_MAX_CHARS = 200

ConfidenceLevel = Literal["high", "medium", "low"]


class Cell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=CELL_MAX_CHARS)
    cite_id: str | None = None


class Table(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=200)
    columns: list[str] = Field(min_length=1, max_length=MAX_COLUMNS)
    rows: list[list[Cell]] = Field(default_factory=list, max_length=MAX_ROWS)

    @model_validator(mode="after")
    def _row_widths(self) -> Self:
        for index, row in enumerate(self.rows):
            if len(row) != len(self.columns):
                raise ValueError(
                    f"row {index} has {len(row)} cells for {len(self.columns)} columns"
                )
        return self


class Chart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["line", "bar"]
    table: int = Field(ge=0)
    x_column: str
    series: list[str] = Field(min_length=1, max_length=MAX_SERIES)


class Confidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: ConfidenceLevel
    note: str = Field(default="", max_length=NOTE_MAX_CHARS)


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Language
    status: AnswerStatus
    summary: str
    points: list[Point] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list, max_length=MAX_TABLES)
    chart: Chart | None = None
    confidence: Confidence | None = None
    unverified: list[str] = Field(default_factory=list, max_length=MAX_UNVERIFIED)
    citations: list[CitationRef] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER

    @classmethod
    def from_answer(cls, answer: Answer) -> "Report":
        return cls.model_validate(answer.model_dump())

    def cite_ids(self) -> list[str]:
        """Every cite id the report uses, in order of first use."""
        ordered = [c.cite_id for c in self.citations]
        ordered += [c for p in self.points for c in p.cite_ids]
        ordered += [c.cite_id for t in self.tables for row in t.rows for c in row if c.cite_id]
        return list(dict.fromkeys(ordered))


def report_json_schema() -> dict[str, Any]:
    return Report.model_json_schema()


def finalize_report(report: Report) -> Report:
    """Server-side overwrite of the disclaimer."""
    return report.model_copy(update={"disclaimer": DISCLAIMER})


def calculation_item(cite_id: str, name: str, output: Decimal) -> SeenItem:
    """A verified calculation as a citable item for the number check."""
    return SeenItem(
        cite_id=cite_id,
        text=f"{name} = {output}",
        value=output,
        source={"kind": "calculation", "name": name},
    )


def cell_number(text: str, language: Language) -> Decimal | None:
    """The one number in a chart cell, with its scale word applied; None if not exactly one."""
    found = [f for f in figures(text, language) if f.value >= 0]
    if len(found) != 1:
        return None
    return found[0].value * found[0].multiplier


_MARKER = re.compile(r"\[(\d+)\]")


@dataclass
class ReportValidation:
    errors: list[str] = field(default_factory=list)
    summary_ok: bool = True
    bad_points: set[int] = field(default_factory=set)
    # (table index, row index) of rows with an unsupported or uncited number.
    bad_rows: set[tuple[int, int]] = field(default_factory=set)
    bad_tables: set[int] = field(default_factory=set)
    chart_ok: bool = True

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_report(
    report: Report,
    seen: Mapping[str, SeenItem],
    calculations: Mapping[str, Decimal],
    language: Language,
    *,
    limit_hit: bool = False,
    derived_ok: bool = True,
) -> ReportValidation:
    """Rules 1 to 6 above. `calculations` maps each verified calculation's `k:` id to its output.

    `derived_ok=False` (deep mode) rejects percentages the model derived itself (FR-008)."""
    items: dict[str, SeenItem] = dict(seen)
    for cite_id, output in calculations.items():
        items[cite_id] = calculation_item(cite_id, cite_id, output)
    result = ReportValidation()

    if report.language != language:
        result.errors.append(f"language must be {language!r}, got {report.language!r}")
    if unknown := sorted(set(report.cite_ids()) - items.keys()):
        result.errors.append(f"unknown cite_id(s) not produced in this run: {unknown}")

    _check_summary(report, items, language, result, derived_ok)
    _check_points(report, items, language, result, derived_ok)
    _check_tables(report, items, language, result, derived_ok)
    _check_chart(report, language, result)

    if report.status == "partial" and not limit_hit:
        level = report.confidence.level if report.confidence else None
        if level != "low" or not report.unverified:
            result.errors.append("a partial report needs confidence low and an unverified list")
    if report.status in ("refused", "no_evidence") and (report.tables or report.chart):
        result.errors.append(f"a {report.status} report has no tables and no chart")
    return result


def _check_summary(
    report: Report,
    items: Mapping[str, SeenItem],
    language: Language,
    result: ReportValidation,
    derived_ok: bool = True,
) -> None:
    markers = {c.marker: c.cite_id for c in report.citations}
    used = {int(m) for m in _MARKER.findall(report.summary)}
    if missing := sorted(used - markers.keys()):
        result.errors.append(f"summary markers without a citation: {missing}")
        result.summary_ok = False
    if any(markers[m] not in items for m in used if m in markers):
        result.summary_ok = False
    cited = [items[c] for c in markers.values() if c in items]
    if bad := unsupported_figures(report.summary, language, cited, derived_ok=derived_ok):
        result.errors.append(f"summary numbers not found in cited sources: {bad}")
        result.summary_ok = False


def _check_points(
    report: Report,
    items: Mapping[str, SeenItem],
    language: Language,
    result: ReportValidation,
    derived_ok: bool = True,
) -> None:
    for index, point in enumerate(report.points):
        if not point.cite_ids:
            result.errors.append(f"point {index} has no cite_ids")
            result.bad_points.add(index)
            continue
        if any(c not in items for c in point.cite_ids):
            result.bad_points.add(index)  # already reported as unknown
            continue
        cited = [items[c] for c in point.cite_ids]
        if bad := unsupported_figures(point.text, language, cited, derived_ok=derived_ok):
            result.errors.append(f"point {index} numbers not found in cited sources: {bad}")
            result.bad_points.add(index)


def _check_tables(
    report: Report,
    items: Mapping[str, SeenItem],
    language: Language,
    result: ReportValidation,
    derived_ok: bool = True,
) -> None:
    for t, table in enumerate(report.tables):
        for r, row in enumerate(table.rows):
            for c, cell in enumerate(row):
                # Malformed numbers ("0.3508" in a Turkish report) are checked too: they never
                # match a source, so the writer is asked to write them in the report's style.
                if not figures(cell.text, language):
                    continue
                where = f"table {t} row {r} column {table.columns[c]!r}"
                if cell.cite_id is None:
                    result.errors.append(f"{where}: a number needs a cite_id")
                    result.bad_rows.add((t, r))
                elif cell.cite_id not in items:
                    result.bad_rows.add((t, r))
                elif bad := unsupported_figures(
                    cell.text, language, [items[cell.cite_id]], derived_ok=derived_ok
                ):
                    result.errors.append(f"{where}: numbers not found in the cited source: {bad}")
                    result.bad_rows.add((t, r))


def _check_chart(report: Report, language: Language, result: ReportValidation) -> None:
    chart = report.chart
    if chart is None:
        return
    if chart.table >= len(report.tables):
        result.errors.append(f"chart table {chart.table} does not exist")
        result.chart_ok = False
        return
    table = report.tables[chart.table]
    missing = [c for c in (chart.x_column, *chart.series) if c not in table.columns]
    if missing:
        result.errors.append(f"chart columns not in table {chart.table}: {missing}")
        result.chart_ok = False
        return
    if not table.rows:
        result.errors.append(f"chart table {chart.table} has no rows")
        result.chart_ok = False
        return
    for name in chart.series:
        column = table.columns.index(name)
        if any(cell_number(row[column].text, language) is None for row in table.rows):
            result.errors.append(f"chart series {name!r} has a non-numeric cell")
            result.chart_ok = False


def to_partial(report: Report, validation: ReportValidation, language: Language) -> Report:
    """Keep only checked content: drop failing points, rows (and emptied tables) and the chart."""
    points = [p for i, p in enumerate(report.points) if i not in validation.bad_points]
    dropped = [p.text for i, p in enumerate(report.points) if i in validation.bad_points]
    tables: list[Table] = []
    index_map: dict[int, int] = {}
    for t, table in enumerate(report.tables):
        rows = [row for r, row in enumerate(table.rows) if (t, r) not in validation.bad_rows]
        dropped += [
            f"{table.title}: " + " | ".join(cell.text for cell in row)
            for r, row in enumerate(table.rows)
            if (t, r) in validation.bad_rows
        ]
        # A table that lost rows keeps only placeholder rows ("—", "no data") unless a kept row
        # still has a number; such a table says nothing, so it goes too.
        lost = len(rows) < len(table.rows)
        if rows and not (
            lost and not any(figures(cell.text, language) for row in rows for cell in row)
        ):
            index_map[t] = len(tables)
            tables.append(table.model_copy(update={"rows": rows}))
    chart = report.chart
    if chart is not None:
        kept = chart.table in index_map and validation.chart_ok
        source = report.tables[chart.table] if chart.table < len(report.tables) else None
        # A table emptied by dropped rows is gone from `index_map`: the chart goes with it.
        unchanged = (
            kept and source is not None and tables[index_map[chart.table]].rows == source.rows
        )
        chart = (
            chart.model_copy(update={"table": index_map[chart.table]})
            if kept and unchanged
            else None
        )

    summary = report.summary if validation.summary_ok and report.language == language else ""
    if not summary and report.summary:
        dropped.insert(0, report.summary)
    used = {c for p in points for c in p.cite_ids} | {
        cell.cite_id for t in tables for row in t.rows for cell in row if cell.cite_id
    }
    citations = [
        c for c in report.citations if c.cite_id in used or (summary and f"[{c.marker}]" in summary)
    ]
    if not summary and not points and not tables:
        summary = NO_VERIFIED_ANSWER[language]
    unverified = [*report.unverified, *dropped][:MAX_UNVERIFIED]
    note = report.confidence.note if report.confidence else ""
    return Report(
        language=language,
        status="partial",
        summary=summary,
        points=points,
        tables=tables,
        chart=chart,
        confidence=Confidence(level="low", note=note),
        unverified=unverified,
        citations=citations,
    )
