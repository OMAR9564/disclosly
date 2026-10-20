"""SEC EDGAR source: 10-K/10-Q filings and companyfacts (research §R1)."""

import json
from datetime import UTC, date, datetime
from typing import Any

from ingestion.config import Company, IngestionSettings
from ingestion.models import DocType, Language
from ingestion.sources.base import RateLimitedClient, RemoteDocument

SEC_DATA_HOST = "data.sec.gov"
SEC_ARCHIVE_HOST = "www.sec.gov"
SEC_RATE_PER_S = 5.0
SUPPORTED_FORMS = {"10-K": DocType.TEN_K, "10-Q": DocType.TEN_Q}


def sec_client(settings: IngestionSettings, **kwargs: Any) -> RateLimitedClient:
    """HTTP client with the declared User-Agent and 5 req/s per SEC host."""
    return RateLimitedClient(
        {SEC_DATA_HOST: SEC_RATE_PER_S, SEC_ARCHIVE_HOST: SEC_RATE_PER_S},
        headers={"User-Agent": settings.require_sec_user_agent()},
        default_rate=SEC_RATE_PER_S,
        **kwargs,
    )


def period_for(form: str, report_date: date) -> str:
    """10-K covers a fiscal year; 10-Q uses the calendar quarter of the report date."""
    if form == "10-K":
        return f"{report_date.year}-FY"
    return f"{report_date.year}-Q{(report_date.month - 1) // 3 + 1}"


def archive_url(cik: str, accession: str, primary_document: str) -> str:
    return (
        f"https://{SEC_ARCHIVE_HOST}/Archives/edgar/data/{int(cik)}/"
        f"{accession.replace('-', '')}/{primary_document}"
    )


class SecSource:
    """Lists and fetches 10-K/10-Q primary documents of one company."""

    def __init__(self, client: RateLimitedClient) -> None:
        self._client = client

    async def list_documents(self, company: Company, since: datetime) -> list[RemoteDocument]:
        if company.sec_cik is None:
            return []
        base = f"https://{SEC_DATA_HOST}/submissions"
        filings = (await self._client.get(f"{base}/CIK{company.sec_cik}.json")).json()["filings"]
        pages = [filings["recent"]]
        # Busy filers (JPM files thousands of 424B2 notes) overflow `recent`; older filings
        # are in extra pages listed under `files`, each with its filing date range.
        for extra in filings.get("files", []):
            if date.fromisoformat(extra["filingTo"]) >= since.date():
                pages.append((await self._client.get(f"{base}/{extra['name']}")).json())
        documents: dict[str, RemoteDocument] = {}
        for page in pages:
            for remote in self._page_documents(company.ticker, company.sec_cik, page, since):
                documents.setdefault(remote.source_url, remote)
        return list(documents.values())

    @staticmethod
    def _page_documents(
        ticker: str, cik: str, page: dict[str, Any], since: datetime
    ) -> list[RemoteDocument]:
        documents: list[RemoteDocument] = []
        for accession, filed, reported, form, primary in zip(
            page["accessionNumber"],
            page["filingDate"],
            page["reportDate"],
            page["form"],
            page["primaryDocument"],
            strict=True,
        ):
            doc_type = SUPPORTED_FORMS.get(form)
            if doc_type is None or not reported:
                continue
            published = datetime.fromisoformat(filed).replace(tzinfo=UTC)
            if published < since:
                continue
            documents.append(
                RemoteDocument(
                    ticker=ticker,
                    source_url=archive_url(cik, accession, primary),
                    doc_type=doc_type,
                    period=period_for(form, date.fromisoformat(reported)),
                    published_at=published,
                    language=Language.EN,
                    extra={"accession": accession, "format": "html"},
                )
            )
        return documents

    async def fetch(self, remote: RemoteDocument) -> bytes:
        response = await self._client.get(remote.source_url)
        return response.content

    async def company_facts(self, company: Company) -> dict[str, Any]:
        """Raw `companyfacts` JSON; mapped to canonical metrics by `ingestion.facts`."""
        if company.sec_cik is None:
            return {}
        response = await self._client.get(
            f"https://{SEC_DATA_HOST}/api/xbrl/companyfacts/CIK{company.sec_cik}.json"
        )
        payload: dict[str, Any] = json.loads(response.content)
        return payload
