"""KAP disclosure bodies (research §R2-R3).

`attachment-detail` returns JSON with the rendered disclosure body as HTML parts. Financial
reports render XBRL statements: one data row per concept with a Turkish label (`content-tr`),
an English label hidden with `display: none`, and raw values in the `title` attribute.
"""

import json
from typing import Any

from selectolax.lexbor import LexborHTMLParser, LexborNode

from ingestion.parse.html import parse_html
from ingestion.parse.pdf import ParseError
from ingestion.parse.types import (
    FALLBACK_SECTION,
    ExtractedTable,
    Paragraph,
    ParsedDocument,
    ParsedSection,
)

UNIT_LABEL = "Sunum Para Birimi"
# Form fields every KAP disclosure repeats; they carry no content and made all material event
# chunks look alike to the embedding model.
BOILERPLATE_LABELS = frozenset(
    {
        "Yapılan Açıklama Güncelleme mi?",
        "Yapılan Açıklama Düzeltme mi?",
        "Konuya İlişkin Daha Önce Yapılan Açıklamanın Tarihi",
        "Yapılan Açıklama Ertelenmiş Bir Açıklama mı?",
    }
)


def _tr_text(node: LexborNode | None) -> str:
    if node is None:
        return ""
    tr = node.css_first(".content-tr")
    return " ".join((tr or node).text(separator=" ").split())


def _statement(part: str, index: int) -> ParsedSection | None:
    tree = LexborHTMLParser(part)
    section = ParsedSection(f"Tablo {index + 1}")
    for row in tree.css("table.financial-header-table tr"):
        cells = row.css("td")
        if len(cells) == 2 and "[" not in cells[0].text():
            label, value = (" ".join(c.text().split()) for c in cells)
            section.blocks.append(Paragraph(f"{label}: {value}"))
    headers = [_tr_text(h) for h in tree.css("td.context-header")]
    if not headers:
        return None  # not a financial statement (e.g. a material event form)
    rows: list[list[str]] = []
    for row in tree.css("tr.data-input-row, tr.abstract-row"):
        label = _tr_text(row.css_first(".taxonomy-field-title"))
        if section.title is None and "abstract-row" in (row.attributes.get("class") or ""):
            section.title = label or None
            continue
        values = []
        for cell in row.css("td.taxonomy-context-value"):
            valued = cell.css_first("[title]")
            values.append((valued.attributes.get("title") or "") if valued else "")
        if not label or not any(values):
            continue
        concept = row.css_first(".taxonomy-field-name")
        concept_name = concept.text(strip=True).split("|")[0] if concept else ""
        footnote = row.css_first(".taxonomy-footnote-value")
        rows.append([label, concept_name, footnote.text(strip=True) if footnote else "", *values])
    if not rows:
        return None
    width = max(len(r) for r in rows)
    header = ["Kalem", "Kavram", "Dipnot", *headers][:width]
    header += [""] * (width - len(header))
    section.blocks.append(
        ExtractedTable(header=header, rows=[r + [""] * (width - len(r)) for r in rows])
    )
    return section


def _form(part: str) -> ParsedSection | None:
    """Non-financial KAP forms (material events, report cover forms): Turkish label/value pairs
    and text blocks, English duplicates skipped."""
    tree = LexborHTMLParser(part)
    section = ParsedSection(FALLBACK_SECTION)
    for row in tree.css("tr.data-input-row, tr.abstract-row"):
        label = _tr_text(row.css_first(".taxonomy-field-title"))
        if "abstract-row" in (row.attributes.get("class") or ""):
            if section.title is None:
                section.title = label or None
            continue
        block = row.css_first("td.taxonomy-context-value-summernote.content-tr")
        if block is not None:
            lines = [
                " ".join(d.text(separator=" ").split())
                for d in block.css(".text-block-value div")
                if not any(
                    child.tag == "div" for child in d.traverse(include_text=False) if child != d
                )
            ]
            leaf = [line for line in dict.fromkeys(lines) if line]
            texts = leaf or [" ".join(block.text(separator=" ").split())]
            if label:
                section.blocks.append(Paragraph(label))
            section.blocks.extend(Paragraph(t) for t in texts if t)
            continue
        if label in BOILERPLATE_LABELS:
            continue
        cell = row.css_first("td.taxonomy-context-value")
        value = " ".join(cell.text(separator=" ").split()) if cell else ""
        if label and value:
            section.blocks.append(Paragraph(f"{label}: {value}"))
    return section if section.blocks else None


def parse_kap_detail(content: bytes) -> ParsedDocument:
    """Parse an `attachment-detail` JSON response into sections."""
    try:
        payload: Any = json.loads(content)
        detail = payload[0] if isinstance(payload, list) else payload
        parts: list[str] = list(detail["disclosureBody"])
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ParseError(f"unexpected KAP detail payload: {type(exc).__name__}") from exc
    statements = [s for i, p in enumerate(parts) if (s := _statement(p, i)) is not None]
    if statements and any(s.tables for s in statements):
        return ParsedDocument(statements)
    sections = [s for p in parts if (s := _form(p)) is not None]
    if not sections:
        for part in parts:
            sections.extend(parse_html(part.encode(), detect_items=False).sections)
    if not any(s.blocks for s in sections):
        raise ParseError("empty KAP disclosure body")
    return ParsedDocument(sections)
