"""Parser output shared by the HTML and PDF parsers and the chunker."""

from dataclasses import dataclass, field

FALLBACK_SECTION = "body"


@dataclass(frozen=True)
class Paragraph:
    text: str
    page: int | None = None


@dataclass(frozen=True)
class ExtractedTable:
    header: list[str]
    rows: list[list[str]]
    page: int | None = None


Block = Paragraph | ExtractedTable


@dataclass
class ParsedSection:
    label: str
    title: str | None = None
    blocks: list[Block] = field(default_factory=list)

    @property
    def pages(self) -> list[int]:
        return [b.page for b in self.blocks if b.page is not None]

    @property
    def page_start(self) -> int | None:
        return min(self.pages, default=None)

    @property
    def page_end(self) -> int | None:
        return max(self.pages, default=None)

    @property
    def tables(self) -> list[ExtractedTable]:
        return [b for b in self.blocks if isinstance(b, ExtractedTable)]


@dataclass
class ParsedDocument:
    sections: list[ParsedSection]

    @property
    def text(self) -> str:
        return "\n".join(
            b.text for s in self.sections for b in s.blocks if isinstance(b, Paragraph)
        )
