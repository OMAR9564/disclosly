"""Section-aware chunker (FR-007, research §R4).

Text is packed sentence by sentence inside one section up to `size` tokens, with up to
`overlap` tokens of trailing sentences repeated at the start of the next chunk. Tables become
their own chunks, split only at row boundaries, with the header repeated in each chunk.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Literal, Protocol

from ingestion.parse.tables import table_text
from ingestion.parse.types import ExtractedTable, Paragraph, ParsedDocument

ChunkKind = Literal["text", "table"]

DEFAULT_SIZE = 600
DEFAULT_OVERLAP = 64


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


class WhitespaceCounter:
    """Counts whitespace-separated words; used in tests and when no tokenizer is loaded."""

    def count(self, text: str) -> int:
        return len(text.split())


class HuggingFaceCounter:
    """Counts tokens with the embedding model's tokenizer (needs the `ml` group)."""

    def __init__(self, model_name: str) -> None:
        from transformers import AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(model_name)

    def count(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=False))


@dataclass(frozen=True)
class ChunkerConfig:
    size: int = DEFAULT_SIZE
    overlap: int = DEFAULT_OVERLAP

    def __post_init__(self) -> None:
        if self.size < 1 or not 0 <= self.overlap < self.size:
            raise ValueError("chunk size must be >= 1 and 0 <= overlap < size")

    @property
    def version(self) -> str:
        return f"v1-{self.size}-{self.overlap}"


@dataclass(frozen=True)
class ChunkDraft:
    id: str
    ordinal: int
    section_index: int
    kind: ChunkKind
    text: str
    token_count: int
    page: int | None


@dataclass(frozen=True)
class _Unit:
    text: str
    tokens: int
    page: int | None


def chunk_id(document_id: int, chunker_version: str, ordinal: int) -> str:
    return hashlib.sha256(f"{document_id}:{chunker_version}:{ordinal}".encode()).hexdigest()


_SENTENCE_RE = re.compile(r"(?<=[.!?;:])\s+")


def _split_words(text: str, limit: int, counter: TokenCounter) -> list[str]:
    """Split a too-long piece into word groups of at most `limit` tokens each."""
    pieces: list[str] = []
    current: list[str] = []
    for word in text.split():
        candidate = " ".join([*current, word])
        if current and counter.count(candidate) > limit:
            pieces.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        pieces.append(" ".join(current))
    return pieces


def _units(paragraph: Paragraph, size: int, counter: TokenCounter) -> list[_Unit]:
    units: list[_Unit] = []
    for sentence in _SENTENCE_RE.split(paragraph.text):
        tokens = counter.count(sentence)
        if tokens == 0:
            continue
        if tokens <= size:
            units.append(_Unit(sentence, tokens, paragraph.page))
            continue
        for piece in _split_words(sentence, size, counter):
            units.append(_Unit(piece, counter.count(piece), paragraph.page))
    return units


def _pack_text(units: list[_Unit], config: ChunkerConfig, counter: TokenCounter) -> list[_Unit]:
    chunks: list[_Unit] = []
    current: list[_Unit] = []
    fresh = 0  # units in `current` that are not overlap from the previous chunk

    def flush() -> None:
        text = " ".join(u.text for u in current)
        chunks.append(_Unit(text, counter.count(text), current[0].page))

    for unit in units:
        if current and sum(u.tokens for u in current) + unit.tokens > config.size:
            if fresh:
                flush()
            tail: list[_Unit] = []
            for previous in reversed(current):
                if sum(u.tokens for u in tail) + previous.tokens > config.overlap:
                    break
                tail.insert(0, previous)
            current = tail
            fresh = 0
            while current and sum(u.tokens for u in current) + unit.tokens > config.size:
                current.pop(0)
        current.append(unit)
        fresh += 1
    if current and fresh:
        flush()
    # The joined text may count differently from the sum; split again where needed.
    result: list[_Unit] = []
    for chunk in chunks:
        if chunk.tokens <= config.size:
            result.append(chunk)
        else:
            result.extend(
                _Unit(p, counter.count(p), chunk.page)
                for p in _split_words(chunk.text, config.size, counter)
            )
    return result


def _pack_table(table: ExtractedTable, size: int, counter: TokenCounter) -> list[_Unit]:
    header = table_text(table, rows=[])
    header_tokens = counter.count(header)
    chunks: list[_Unit] = []
    rows: list[list[str]] = []

    def flush() -> None:
        text = table_text(table, rows=rows)
        chunks.append(_Unit(text, counter.count(text), table.page))

    for row in table.rows:
        if counter.count(table_text(table, rows=[*rows, row])) <= size:
            rows.append(row)
            continue
        if rows:
            flush()
            rows = []
        if counter.count(table_text(table, rows=[row])) <= size:
            rows = [row]
            continue
        # One row longer than a chunk: the only case where a row is split. Header kept
        # when it fits next to a piece.
        row_text = " | ".join(row)
        budget = size - header_tokens - 1 if header_tokens < size // 2 else size
        for piece in _split_words(row_text, max(1, budget), counter):
            text = f"{header}\n{piece}" if budget < size else piece
            chunks.append(_Unit(text, counter.count(text), table.page))
    if rows:
        flush()
    return chunks


def chunk_document(
    document_id: int,
    parsed: ParsedDocument,
    config: ChunkerConfig,
    counter: TokenCounter,
) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []

    def add(section_index: int, kind: ChunkKind, unit: _Unit) -> None:
        ordinal = len(drafts)
        drafts.append(
            ChunkDraft(
                id=chunk_id(document_id, config.version, ordinal),
                ordinal=ordinal,
                section_index=section_index,
                kind=kind,
                text=unit.text,
                token_count=unit.tokens,
                page=unit.page,
            )
        )

    for index, section in enumerate(parsed.sections):
        pending: list[_Unit] = []
        for block in section.blocks:
            if isinstance(block, Paragraph):
                pending.extend(_units(block, config.size, counter))
                continue
            for unit in _pack_text(pending, config, counter):
                add(index, "text", unit)
            pending = []
            for unit in _pack_table(block, config.size, counter):
                add(index, "table", unit)
        for unit in _pack_text(pending, config, counter):
            add(index, "text", unit)
    return drafts
