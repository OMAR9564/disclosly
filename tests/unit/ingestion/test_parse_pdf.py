import shutil
from pathlib import Path

import pytest
from PIL import Image

from ingestion.parse.pdf import ParseError, parse_pdf, tesseract_ocr
from ingestion.parse.types import Paragraph

FIXTURES = Path(__file__).parents[2] / "fixtures" / "pdf"


def _never_ocr(_: list[Image.Image]) -> str:
    raise AssertionError("OCR must not run on text pages")


def test_text_pdf_pages_numbered_without_ocr() -> None:
    doc = parse_pdf((FIXTURES / "text-report.pdf").read_bytes(), ocr=_never_ocr)
    blocks = [b for b in doc.sections[0].blocks if isinstance(b, Paragraph)]
    assert doc.sections[0].label == "body"
    assert {b.page for b in blocks} == {1, 2}
    assert "Hasilat 1.000.000 TL" in doc.text
    assert doc.sections[0].page_start == 1 and doc.sections[0].page_end == 2


def test_image_page_goes_to_ocr() -> None:
    calls: list[int] = []

    def fake_ocr(images: list[Image.Image]) -> str:
        calls.append(len(images))
        return "KAMUYU AYDINLATMA\n\nHasılat 1000 TL"

    doc = parse_pdf((FIXTURES / "scanned-page.pdf").read_bytes(), ocr=fake_ocr)
    assert calls == [1]
    assert [b.text for b in doc.sections[0].blocks if isinstance(b, Paragraph)] == [
        "KAMUYU AYDINLATMA",
        "Hasılat 1000 TL",
    ]


def test_ocr_failure_fails_document() -> None:
    def broken(_: list[Image.Image]) -> str:
        raise RuntimeError("tesseract missing")

    with pytest.raises(ParseError, match="OCR failed on page 1"):
        parse_pdf((FIXTURES / "scanned-page.pdf").read_bytes(), ocr=broken)


def test_empty_ocr_result_fails_instead_of_storing_empty_text() -> None:
    with pytest.raises(ParseError, match="no text"):
        parse_pdf((FIXTURES / "scanned-page.pdf").read_bytes(), ocr=lambda _: "  ")


def test_unreadable_pdf_fails() -> None:
    with pytest.raises(ParseError, match="unreadable"):
        parse_pdf(b"not a pdf")


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract not installed")
def test_real_tesseract_reads_scanned_page() -> None:
    doc = parse_pdf((FIXTURES / "scanned-page.pdf").read_bytes(), ocr=tesseract_ocr)
    assert "AYDINLATMA" in doc.text.upper()
