"""Turn raw table cells into header + rows (data-model.md `tables`)."""

from ingestion.parse.types import ExtractedTable

# Cells that only hold a currency or percent sign split from their number in SEC HTML.
_NOISE_CELLS = {"$", "%", ")", "€", "₺", "TL"}


def _clean(cell: str) -> str:
    return " ".join(cell.split())


def build_table(
    raw_rows: list[list[str]], *, header_rows: int = 1, page: int | None = None
) -> ExtractedTable | None:
    """Clean cells, blank sign-only cells, drop empty rows and columns; pad rows to one width.

    Returns None when fewer than one header row plus one data row remain.
    """
    rows = [["" if _clean(c) in _NOISE_CELLS else _clean(c) for c in row] for row in raw_rows]
    rows = [row for row in rows if any(row)]
    if len(rows) <= header_rows:
        return None
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    keep = [i for i in range(width) if any(row[i] for row in rows)]
    rows = [[row[i] for i in keep] for row in rows]
    header = [" ".join(filter(None, parts)) for parts in zip(*rows[:header_rows], strict=True)]
    return ExtractedTable(header=header, rows=rows[header_rows:], page=page)


def table_text(table: ExtractedTable, rows: list[list[str]] | None = None) -> str:
    """Pipe-separated text used for chunks; header first."""
    lines = [" | ".join(table.header)]
    lines.extend(" | ".join(row) for row in (table.rows if rows is None else rows))
    return "\n".join(lines)
