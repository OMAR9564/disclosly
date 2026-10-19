"""PDF text per page with pypdf; near-empty pages go to Tesseract OCR (research §R3)."""

import io
import re
from collections.abc import Callable

from PIL import Image
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ingestion.parse.types import FALLBACK_SECTION, Paragraph, ParsedDocument, ParsedSection

MIN_PAGE_CHARS = 50
OCR_LANGUAGES = "tur+eng"

# Takes the page images and returns the recognised text.
Ocr = Callable[[list[Image.Image]], str]


class ParseError(Exception):
    """The document cannot be turned into text; the pipeline marks it failed."""


def tesseract_ocr(images: list[Image.Image]) -> str:
    import pytesseract

    return "\n\n".join(pytesseract.image_to_string(img, lang=OCR_LANGUAGES) for img in images)


_BLANK_LINES_RE = re.compile(r"\n\s*\n")


def _paragraphs(text: str, page: int) -> list[Paragraph]:
    parts = (" ".join(p.split()) for p in _BLANK_LINES_RE.split(text))
    return [Paragraph(p, page=page) for p in parts if p]


def _page_images(page: object) -> list[Image.Image]:
    return [img.image for img in getattr(page, "images", []) if img.image is not None]


def parse_pdf(content: bytes, *, ocr: Ocr = tesseract_ocr) -> ParsedDocument:
    """One `body` section with one paragraph per text block, pages numbered from 1.

    Raises ParseError when the PDF is unreadable, OCR fails, or no text remains.
    """
    try:
        reader = PdfReader(io.BytesIO(content))
        pages = list(reader.pages)
    except (PdfReadError, ValueError, OSError) as exc:
        raise ParseError(f"unreadable PDF: {type(exc).__name__}") from exc

    blocks: list[Paragraph] = []
    for number, page in enumerate(pages, start=1):
        text = page.extract_text() or ""
        if len(text.strip()) < MIN_PAGE_CHARS:
            images = _page_images(page)
            if images:
                try:
                    text = ocr(images)
                except Exception as exc:
                    raise ParseError(f"OCR failed on page {number}: {type(exc).__name__}") from exc
        blocks.extend(_paragraphs(text, number))

    if not blocks:
        raise ParseError("no text extracted")
    return ParsedDocument([ParsedSection(FALLBACK_SECTION, blocks=list(blocks))])
