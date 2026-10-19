from pathlib import Path

from ingestion.parse.html import parse_html
from ingestion.parse.tables import build_table, table_text
from ingestion.parse.types import FALLBACK_SECTION, ExtractedTable, Paragraph

FIXTURE = Path(__file__).parents[2] / "fixtures" / "sec" / "aapl-10k-trimmed.htm"


def test_10k_items_detected_in_order() -> None:
    doc = parse_html(FIXTURE.read_bytes())
    labels = [s.label for s in doc.sections]
    assert labels == [FALLBACK_SECTION, "Item 1", "Item 1A", "Item 7", "Item 7A", "Item 8"]
    item7 = doc.sections[3]
    assert item7.title is not None and item7.title.startswith("Management")
    assert any(
        "Total net sales increased" in b.text for b in item7.blocks if isinstance(b, Paragraph)
    )


def test_tables_extracted_with_header_and_rows() -> None:
    doc = parse_html(FIXTURE.read_bytes())
    by_label = {s.label: s for s in doc.sections}
    mdna = by_label["Item 7"].tables[0]
    assert mdna.header == ["", "2025", "Change", "2024"]
    assert ["iPhone", "$ 209,586", "4 %", "$ 201,183"] in mdna.rows
    statement = by_label["Item 8"].tables[0]
    assert statement.header == ["Line item", "2025", "2024"]
    assert statement.rows[0] == ["Net sales", "416,161", "391,035"]


def test_hidden_script_and_xbrl_header_ignored() -> None:
    text = parse_html(FIXTURE.read_bytes()).text
    assert "hidden xbrl header" not in text
    assert "ignored" not in text


def test_no_items_falls_back_to_body() -> None:
    doc = parse_html(b"<html><body><p>Hello</p><div>World</div></body></html>")
    assert [s.label for s in doc.sections] == [FALLBACK_SECTION]
    assert doc.text == "Hello\nWorld"
    assert parse_html(b"<p>Item 1. Not a heading here</p>", detect_items=False).sections[
        0
    ].label == (FALLBACK_SECTION)


def test_empty_document_has_one_empty_body() -> None:
    doc = parse_html(b"")
    assert [s.label for s in doc.sections] == [FALLBACK_SECTION]
    assert doc.sections[0].blocks == []


def test_build_table_cleans_cells() -> None:
    table = build_table([["Metric", "", "2025"], ["Revenue", "$", " 10 "], ["", "", ""]])
    assert table == ExtractedTable(header=["Metric", "2025"], rows=[["Revenue", "10"]])
    assert build_table([["only header"]]) is None
    assert table_text(table) == "Metric | 2025\nRevenue | 10"
    assert table_text(table, rows=[]) == "Metric | 2025"
