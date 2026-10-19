from pathlib import Path

import pytest

from ingestion.chunk import ChunkerConfig, WhitespaceCounter, chunk_document, chunk_id
from ingestion.parse.html import parse_html
from ingestion.parse.types import ExtractedTable, Paragraph, ParsedDocument, ParsedSection

COUNTER = WhitespaceCounter()
FIXTURE = Path(__file__).parents[2] / "fixtures" / "sec" / "aapl-10k-trimmed.htm"


def _words(n: int, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{i}." for i in range(n))


def _doc(*blocks: Paragraph | ExtractedTable, label: str = "body") -> ParsedDocument:
    return ParsedDocument([ParsedSection(label, blocks=list(blocks))])


def test_config_version_and_validation() -> None:
    assert ChunkerConfig().version == "v1-600-64"
    assert ChunkerConfig(400, 32).version == "v1-400-32"
    with pytest.raises(ValueError):
        ChunkerConfig(10, 10)
    with pytest.raises(ValueError):
        ChunkerConfig(0, 0)


def test_no_chunk_above_max_and_overlap_repeats_tail() -> None:
    config = ChunkerConfig(size=20, overlap=5)
    chunks = chunk_document(1, _doc(Paragraph(_words(100), page=3)), config, COUNTER)
    assert len(chunks) > 1
    assert all(c.token_count <= 20 for c in chunks)
    assert all(c.kind == "text" and c.page == 3 for c in chunks)
    first, second = chunks[0].text.split(), chunks[1].text.split()
    assert second[0] in first  # overlap from the previous chunk
    assert {w for c in chunks for w in c.text.split()} == set(_words(100).split())


def test_long_sentence_split_into_word_groups() -> None:
    config = ChunkerConfig(size=10, overlap=0)
    long_sentence = " ".join(f"x{i}" for i in range(35))
    chunks = chunk_document(1, _doc(Paragraph(long_sentence)), config, COUNTER)
    assert [c.token_count for c in chunks] == [10, 10, 10, 5]


def test_stable_ids() -> None:
    doc = _doc(Paragraph(_words(50)))
    config = ChunkerConfig(size=20, overlap=4)
    a = chunk_document(7, doc, config, COUNTER)
    b = chunk_document(7, doc, config, COUNTER)
    assert [c.id for c in a] == [c.id for c in b]
    assert a[0].id == chunk_id(7, "v1-20-4", 0)
    assert len(a[0].id) == 64
    assert chunk_document(8, doc, config, COUNTER)[0].id != a[0].id
    assert len({c.id for c in a}) == len(a)


def test_table_rows_never_split_and_header_repeated() -> None:
    table = ExtractedTable(
        header=["Kalem", "2025", "2024"],
        rows=[[f"Satır{i}", str(i), str(i * 2)] for i in range(30)],
        page=5,
    )
    config = ChunkerConfig(size=40, overlap=0)
    chunks = chunk_document(1, _doc(Paragraph("Giriş metni."), table), config, COUNTER)
    table_chunks = [c for c in chunks if c.kind == "table"]
    assert chunks[0].kind == "text"
    assert len(table_chunks) > 1  # oversized table case: split at rows
    seen_rows: list[str] = []
    for chunk in table_chunks:
        lines = chunk.text.split("\n")
        assert lines[0] == "Kalem | 2025 | 2024"
        assert chunk.token_count <= 40 and chunk.page == 5
        seen_rows.extend(lines[1:])
    assert seen_rows == [" | ".join(r) for r in table.rows]


def test_single_oversized_row_is_split_but_bounded() -> None:
    table = ExtractedTable(header=["A", "B"], rows=[["word " * 30, "x"], ["short", "y"]])
    chunks = chunk_document(1, _doc(table), ChunkerConfig(size=12, overlap=0), COUNTER)
    assert all(c.token_count <= 12 for c in chunks)
    assert chunks[-1].text == "A | B\nshort | y"


def test_sections_keep_their_index_and_10k_fixture() -> None:
    chunks = chunk_document(3, parse_html(FIXTURE.read_bytes()), ChunkerConfig(), COUNTER)
    assert {c.section_index for c in chunks} == {0, 1, 2, 3, 4, 5}
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert sum(c.kind == "table" for c in chunks) == 2
