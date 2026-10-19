"""Fixture sources for pipeline tests: no network."""

from datetime import UTC, datetime
from pathlib import Path

from ingestion.config import Company
from ingestion.models import DocType, Language
from ingestion.sources.base import RemoteDocument

FIXTURES = Path(__file__).parents[2] / "fixtures"
AAPL = Company(ticker="AAPL", name="Apple Inc.", market="us", sec_cik="0000320193")
THYAO = Company(ticker="THYAO", name="Türk Hava Yolları", market="bist", kap_member_id="m-thyao")
SEC_URL = "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm"
KAP_URL = "https://www.kap.org.tr/tr/api/file/download/thyao-2025-fy"


def sec_remote() -> RemoteDocument:
    return RemoteDocument(
        ticker="AAPL",
        source_url=SEC_URL,
        doc_type=DocType.TEN_K,
        period="2025-FY",
        published_at=datetime(2025, 10, 31, tzinfo=UTC),
        language=Language.EN,
        extra={"accession": "0000320193-25-000079", "format": "html"},
    )


def kap_remote(url: str = KAP_URL) -> RemoteDocument:
    return RemoteDocument(
        ticker="THYAO",
        source_url=url,
        doc_type=DocType.KAP_ANNUAL_REPORT,
        period="2025-FY",
        published_at=datetime(2026, 3, 1, tzinfo=UTC),
        language=Language.TR,
        extra={"format": "pdf"},
    )


class FakeSource:
    """Serves fixed documents; `bodies` can be changed between runs."""

    def __init__(
        self, ticker: str, remotes: list[RemoteDocument], bodies: dict[str, bytes]
    ) -> None:
        self.ticker = ticker
        self.remotes = remotes
        self.bodies = bodies
        self.fetches: list[str] = []
        self.fail_urls: set[str] = set()

    async def list_documents(self, company: Company, since: datetime) -> list[RemoteDocument]:
        return [r for r in self.remotes if r.ticker == company.ticker]

    async def fetch(self, remote: RemoteDocument) -> bytes:
        self.fetches.append(remote.source_url)
        if remote.source_url in self.fail_urls:
            raise ConnectionError("simulated network error")
        return self.bodies[remote.source_url]
