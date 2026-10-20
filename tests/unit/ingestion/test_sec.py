from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from ingestion.config import Company, IngestionSettings
from ingestion.models import DocType, Language
from ingestion.sources.sec import SecSource, archive_url, period_for, sec_client

FIXTURES = Path(__file__).parents[2] / "fixtures" / "sec"
AAPL = Company(ticker="AAPL", name="Apple Inc.", market="us", sec_cik="0000320193")


def _handler(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/submissions/CIK0000320193.json":
            return httpx.Response(
                200, content=(FIXTURES / "submissions_CIK0000320193.json").read_bytes()
            )
        if path == "/api/xbrl/companyfacts/CIK0000320193.json":
            return httpx.Response(
                200, content=(FIXTURES / "companyfacts_CIK0000320193.json").read_bytes()
            )
        if path.endswith(".htm"):
            return httpx.Response(200, content=(FIXTURES / "aapl-10k-trimmed.htm").read_bytes())
        return httpx.Response(404)

    return httpx.MockTransport(handle)


async def _noop_sleep(_: float) -> None:
    return None


def _source(seen: list[httpx.Request]) -> SecSource:
    settings = IngestionSettings(sec_user_agent="Disclosly ops@example.com")
    return SecSource(sec_client(settings, transport=_handler(seen), sleep=_noop_sleep))


async def test_lists_only_10k_10q_within_window() -> None:
    seen: list[httpx.Request] = []
    docs = await _source(seen).list_documents(AAPL, datetime(2024, 10, 19, tzinfo=UTC))

    assert [d.doc_type for d in docs] == [DocType.TEN_K, DocType.TEN_Q, DocType.TEN_K]
    assert [d.period for d in docs] == ["2025-FY", "2025-Q2", "2024-FY"]
    assert all(d.language is Language.EN and d.ticker == "AAPL" for d in docs)
    assert docs[0].source_url == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm"
    )
    assert docs[0].extra["accession"] == "0000320193-25-000079"
    assert seen[0].headers["User-Agent"] == "Disclosly ops@example.com"


async def test_fetch_returns_bytes_and_company_facts() -> None:
    seen: list[httpx.Request] = []
    source = _source(seen)
    docs = await source.list_documents(AAPL, datetime(2025, 1, 1, tzinfo=UTC))
    body = await source.fetch(docs[0])
    facts = await source.company_facts(AAPL)

    assert b"Item 7." in body
    assert facts["entityName"] == "Apple Inc."


async def test_company_without_cik_lists_nothing() -> None:
    thy = Company(ticker="THYAO", name="THY", market="bist", kap_member_id="x")
    source = _source([])
    assert await source.list_documents(thy, datetime(2024, 1, 1, tzinfo=UTC)) == []
    assert await source.company_facts(thy) == {}


def test_missing_user_agent_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="SEC_USER_AGENT"):
        sec_client(IngestionSettings())


def test_period_and_url_helpers() -> None:
    assert period_for("10-K", date(2025, 9, 27)) == "2025-FY"
    assert period_for("10-Q", date(2025, 12, 27)) == "2025-Q4"
    assert period_for("10-Q", date(2025, 3, 29)) == "2025-Q1"
    assert archive_url("0000320193", "0000320193-25-000079", "a.htm").endswith(
        "/320193/000032019325000079/a.htm"
    )


def _columns(*rows: tuple[str, str, str, str, str]) -> dict[str, list[str]]:
    keys = ("accessionNumber", "filingDate", "reportDate", "form", "primaryDocument")
    return {key: [row[i] for row in rows] for i, key in enumerate(keys)}


async def test_reads_older_submission_pages_inside_window() -> None:
    jpm = Company(ticker="JPM", name="JPMorgan Chase", market="us", sec_cik="0000019617")
    recent = _columns(
        ("0000019617-25-000900", "2025-10-01", "", "424B2", "n.htm"),
        ("0000019617-25-000800", "2025-08-01", "2025-06-30", "10-Q", "q2.htm"),
    )
    page_one = _columns(
        ("0000019617-25-000800", "2025-08-01", "2025-06-30", "10-Q", "q2.htm"),  # overlap
        ("0000019617-25-000100", "2025-02-14", "2024-12-31", "10-K", "k.htm"),
    )
    index = {
        "filings": {
            "recent": recent,
            "files": [
                {"name": "CIK0000019617-submissions-001.json", "filingFrom": "2024-06-01",
                 "filingTo": "2025-08-01"},
                {"name": "CIK0000019617-submissions-002.json", "filingFrom": "2010-01-01",
                 "filingTo": "2024-05-31"},
            ],
        }
    }  # fmt: skip
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/submissions/CIK0000019617.json":
            return httpx.Response(200, json=index)
        if request.url.path == "/submissions/CIK0000019617-submissions-001.json":
            return httpx.Response(200, json=page_one)
        return httpx.Response(404)

    settings = IngestionSettings(sec_user_agent="Disclosly ops@example.com")
    client = sec_client(settings, transport=httpx.MockTransport(handle), sleep=_noop_sleep)
    docs = await SecSource(client).list_documents(jpm, datetime(2024, 10, 19, tzinfo=UTC))

    assert [(d.doc_type, d.period) for d in docs] == [
        (DocType.TEN_Q, "2025-Q2"),
        (DocType.TEN_K, "2024-FY"),
    ]
    assert seen == [  # the page that ends before the window is never fetched
        "/submissions/CIK0000019617.json",
        "/submissions/CIK0000019617-submissions-001.json",
    ]
