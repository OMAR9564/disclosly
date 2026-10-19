"""HTML to sections and tables with selectolax (research §R3).

10-K/10-Q text is split at "Item N." headings; documents without them get one `body` section.
"""

import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from ingestion.parse.tables import build_table
from ingestion.parse.types import (
    FALLBACK_SECTION,
    Block,
    ExtractedTable,
    Paragraph,
    ParsedDocument,
    ParsedSection,
)

_ITEM_RE = re.compile(r"^item\s+(\d{1,2}[a-c]?)\s*[.:\-—]\s*(.*)$", re.IGNORECASE)
_MAX_HEADING_CHARS = 200
_BLOCK_TAGS = {
    "p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "section",
    "article", "dd", "dt",
}  # fmt: skip
_SKIP_TAGS = {"script", "style", "noscript", "head", "template"}
_HIDDEN_RE = re.compile(r"display\s*:\s*none", re.IGNORECASE)


def _hidden(node: LexborNode) -> bool:
    return bool(_HIDDEN_RE.search(node.attributes.get("style") or ""))


def _has_block_child(node: LexborNode) -> bool:
    return any(
        child.tag in _BLOCK_TAGS or child.tag == "table" for child in node.iter(include_text=False)
    )


def _table(node: LexborNode) -> ExtractedTable | None:
    raw: list[list[str]] = []
    header_rows = 0
    for tr in node.css("tr"):
        cells = tr.css("th, td")
        if not cells:
            continue
        if not raw and all(c.tag == "th" for c in cells):
            header_rows = 1
        raw.append([c.text(separator=" ") for c in cells])
    return build_table(raw, header_rows=header_rows or 1)


def _walk(node: LexborNode, out: list[Block]) -> None:
    for child in node.iter(include_text=False):
        if child.tag in _SKIP_TAGS or (child.tag or "").startswith("ix:header") or _hidden(child):
            continue
        if child.tag == "table":
            table = _table(child)
            if table is not None:
                out.append(table)
        elif child.tag in _BLOCK_TAGS and not _has_block_child(child):
            text = " ".join(child.text(separator=" ").split())
            if text:
                out.append(Paragraph(text))
        else:
            _walk(child, out)


def split_items(blocks: list[Block]) -> list[ParsedSection]:
    """Group blocks into sections at "Item N." headings; text before the first item is `body`."""
    sections: list[ParsedSection] = [ParsedSection(FALLBACK_SECTION)]
    for block in blocks:
        if isinstance(block, Paragraph) and len(block.text) <= _MAX_HEADING_CHARS:
            match = _ITEM_RE.match(block.text)
            if match is not None:
                label = f"Item {match.group(1).upper()}"
                sections.append(ParsedSection(label, title=match.group(2) or None))
                continue
        sections[-1].blocks.append(block)
    return [s for s in sections if s.blocks or s.label != FALLBACK_SECTION]


def parse_html(content: bytes, *, detect_items: bool = True) -> ParsedDocument:
    tree = LexborHTMLParser(content.decode("utf-8", errors="replace"))
    blocks: list[Block] = []
    if tree.body is not None:
        _walk(tree.body, blocks)
    if not detect_items:
        return ParsedDocument([ParsedSection(FALLBACK_SECTION, blocks=blocks)])
    sections = split_items(blocks)
    return ParsedDocument(sections or [ParsedSection(FALLBACK_SECTION)])
