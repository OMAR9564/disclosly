import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from ingestion.config import Company
from ingestion.models import DocType, Language
from ingestion.parse.kap import parse_kap_detail
from ingestion.parse.pdf import ParseError
from ingestion.parse.types import ExtractedTable, Paragraph
from ingestion.sources.kap import (
    KapSource,
    _turkish_pdf,
    _windows,
    classify,
    kap_client,
    parse_publish_date,
    period_of,
)

FIXTURES = Path(__file__).parents[2] / "fixtures" / "kap"
THYAO = Company(
    ticker="THYAO", name="THY", market="bist", kap_member_id="4028e4a140f2ed720140f376bebb01a7"
)
PDF = (FIXTURES.parent / "pdf" / "text-report.pdf").read_bytes()


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def _source(seen: list[httpx.Request]) -> KapSource:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/tr/api/disclosure/members/byCriteria":
            body = json.loads(request.content)
            if body["fromDate"] < "2025-10-20":
                return httpx.Response(200, json=[])
            return httpx.Response(200, content=(FIXTURES / "listing_THYAO.json").read_bytes())
        if path.startswith("/tr/api/notification/attachment-detail/"):
            name = {
                "1566094": "detail_annual_report.json",
                "1682832": "detail_material_event.json",
            }.get(path.rsplit("/", 1)[-1], "detail_financial_report.json")
            return httpx.Response(200, content=(FIXTURES / name).read_bytes())
        if path.startswith("/tr/api/file/download/"):
            return httpx.Response(200, content=PDF)
        return httpx.Response(404)

    fake = FakeTime()
    client = kap_client(transport=httpx.MockTransport(handle), clock=fake.clock, sleep=fake.sleep)
    return KapSource(client, today=lambda: date(2026, 10, 19))


async def test_lists_in_scope_disclosures_in_one_year_windows() -> None:
    seen: list[httpx.Request] = []
    docs = await _source(seen).list_documents(THYAO, datetime(2024, 10, 19, tzinfo=UTC))

    listings = [r for r in seen if r.method == "POST"]
    assert len(listings) == 3  # windows of at most one year
    bodies = [json.loads(r.content) for r in listings]
    assert bodies[0]["mkkMemberOidList"] == [THYAO.kap_member_id]
    assert bodies[0]["fromDate"] == "2024-10-19"
    assert bodies[-1]["toDate"] == "2026-10-19"

    by_type = {d.doc_type: d for d in docs}
    assert set(by_type) == {
        DocType.KAP_MATERIAL_EVENT,
        DocType.KAP_ANNUAL_REPORT,
        DocType.KAP_FINANCIAL_REPORT,
    }
    assert len(docs) == 5  # circuit breaker, responsibility statement and fund excluded
    assert all(d.language is Language.TR and d.ticker == "THYAO" for d in docs)
    assert by_type[DocType.KAP_ANNUAL_REPORT].extra["format"] == "pdf"
    assert {d.period for d in docs if d.doc_type is DocType.KAP_FINANCIAL_REPORT} == {
        "2025-FY",
        "2025-Q3",
    }
    event = by_type[DocType.KAP_MATERIAL_EVENT]
    assert event.source_url == "https://www.kap.org.tr/tr/Bildirim/1682832"
    assert seen[0].headers["Accept-Language"] == "tr"


async def test_fetch_returns_detail_json_or_turkish_pdf() -> None:
    seen: list[httpx.Request] = []
    source = _source(seen)
    docs = await source.list_documents(THYAO, datetime(2025, 10, 20, tzinfo=UTC))
    by_type = {d.doc_type: d for d in docs}

    event_body = await source.fetch(by_type[DocType.KAP_MATERIAL_EVENT])
    assert json.loads(event_body)[0]["disclosure"]["disclosureBasic"]["disclosureIndex"] == 1682832

    pdf = await source.fetch(by_type[DocType.KAP_ANNUAL_REPORT])
    assert pdf.startswith(b"%PDF")
    assert seen[-1].url.path == "/tr/api/file/download/4028328d9c81f588019cba06fc9617fe"


async def test_company_without_member_id_lists_nothing() -> None:
    aapl = Company(ticker="AAPL", name="Apple", market="us", sec_cik="0000320193")
    assert await _source([]).list_documents(aapl, datetime(2025, 1, 1, tzinfo=UTC)) == []


def test_classify_and_period() -> None:
    assert classify({"subject": "Finansal Rapor", "disclosureClass": "FR"}) is (
        DocType.KAP_FINANCIAL_REPORT
    )
    assert classify({"subject": "İzahname", "disclosureClass": "ODA"}) is DocType.KAP_PROSPECTUS
    assert classify({"subject": "Finansal Rapor", "disclosureClass": "FR", "fundCode": "X"}) is None
    assert classify({"subject": "x", "disclosureClass": "DKB"}) is None
    published = parse_publish_date("07.11.2025 08:03:09")
    assert published == datetime(2025, 11, 7, 5, 3, 9, tzinfo=UTC)
    assert period_of({"year": 2025, "ruleType": "6 Aylık"}, published) == "2025-Q2"
    assert period_of({"year": None, "ruleType": "-"}, published) == "2025-Q4"


def test_windows_and_pdf_choice() -> None:
    assert _windows(date(2025, 1, 1), date(2025, 1, 1)) == [(date(2025, 1, 1), date(2025, 1, 1))]
    assert len(_windows(date(2024, 1, 1), date(2025, 12, 31))) == 3
    english = {"objId": "e", "fileName": "THY AO İng Eylül 2025.pdf", "fileExtension": "pdf"}
    turkish = {"objId": "t", "fileName": "THY AO Eylül 2025.pdf", "fileExtension": "pdf"}
    assert _turkish_pdf([english, turkish]) == turkish
    assert _turkish_pdf([english]) == english
    assert _turkish_pdf([]) is None


def test_parse_financial_report_statements() -> None:
    doc = parse_kap_detail((FIXTURES / "detail_financial_report.json").read_bytes())
    titles = [s.title for s in doc.sections]
    assert titles[0] == "Finansal Durum Tablosu (Bilanço)"
    income = doc.sections[1].tables[0]
    assert income.header[:4] == ["Kalem", "Kavram", "Dipnot", "Cari Dönem 01.01.2025 - 30.09.2025"]
    assert income.rows[0] == [
        "Hasılat", "ifrs-full_Revenue", "19", "690825", "551928", "282789", "221815",
    ]  # fmt: skip
    assert Paragraph("Sunum Para Birimi: 1.000.000 TL") in doc.sections[0].blocks
    assert not any(r[0] == "Revenue" for r in income.rows)  # hidden English labels skipped


def test_parse_material_event_text() -> None:
    doc = parse_kap_detail((FIXTURES / "detail_material_event.json").read_bytes())
    texts = [b.text for b in doc.sections[0].blocks if isinstance(b, Paragraph)]
    assert doc.sections[0].title == "Özel Durum Açıklaması (Genel)"
    assert any("CFM International" in t for t in texts)
    assert not any(isinstance(b, ExtractedTable) for b in doc.sections[0].blocks)
    assert len(texts) == len(set(texts))
    # Form fields repeated by every disclosure are dropped.
    assert not any(
        t.startswith("Yapılan Açıklama") or t.startswith("Konuya İlişkin") for t in texts
    )


def test_parse_bad_payload_fails() -> None:
    with pytest.raises(ParseError):
        parse_kap_detail(b"{}")
    with pytest.raises(ParseError, match="empty"):
        parse_kap_detail(json.dumps([{"disclosureBody": ["<p></p>"]}]).encode())
