"""KAP source (research §R2): public kap.org.tr web API, 1 request per second.

Listing: `POST /tr/api/disclosure/members/byCriteria` per member, in windows of at most one
year (the endpoint rejects longer ranges). Detail: `GET /tr/api/notification/attachment-detail/
{disclosureIndex}` returns the rendered body and attachments; attachments download from
`GET /tr/api/file/download/{objId}`. The endpoints are undocumented and may change; failures
stay per document (FR-011).
"""

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ingestion.config import Company
from ingestion.models import DocType, Language
from ingestion.sources.base import RateLimitedClient, RemoteDocument

KAP_HOST = "www.kap.org.tr"
KAP_BASE = f"https://{KAP_HOST}/tr"
KAP_RATE_PER_S = 1.0
MAX_WINDOW_DAYS = 364
ISTANBUL = ZoneInfo("Europe/Istanbul")
_RULE_TYPES = {"3 Aylık": "Q1", "6 Aylık": "Q2", "9 Aylık": "Q3", "Yıllık": "FY"}
_ENGLISH_MARKERS = ("ing ", "ing.", "ingilizce", "english", " eng ", "_eng", "-eng")


def kap_client(user_agent: str = "Disclosly", **kwargs: Any) -> RateLimitedClient:
    return RateLimitedClient(
        {KAP_HOST: KAP_RATE_PER_S},
        headers={"User-Agent": user_agent, "Accept-Language": "tr"},
        default_rate=KAP_RATE_PER_S,
        **kwargs,
    )


def classify(item: dict[str, Any]) -> DocType | None:
    """Map a listing item to a document type; None for out-of-scope items (funds included)."""
    if item.get("fundCode"):
        return None
    subject = str(item.get("subject") or "")
    klass = item.get("disclosureClass")
    if "İzahname" in subject:
        return DocType.KAP_PROSPECTUS
    if klass == "FR" and subject == "Finansal Rapor":
        return DocType.KAP_FINANCIAL_REPORT
    if klass == "FR" and subject.startswith("Faaliyet Raporu"):
        return DocType.KAP_ANNUAL_REPORT
    if klass == "ODA" and item.get("disclosureCategory") == "ODA":
        return DocType.KAP_MATERIAL_EVENT
    return None


def period_of(item: dict[str, Any], published: datetime) -> str:
    year, rule = item.get("year"), _RULE_TYPES.get(str(item.get("ruleType")))
    if year and rule:
        return f"{year}-{rule}"
    return f"{published.year}-Q{(published.month - 1) // 3 + 1}"


def parse_publish_date(raw: str) -> datetime:
    return datetime.strptime(raw, "%d.%m.%Y %H:%M:%S").replace(tzinfo=ISTANBUL).astimezone(UTC)


def disclosure_url(index: int | str) -> str:
    """Public page of a disclosure; used as the citation URL."""
    return f"https://{KAP_HOST}/tr/Bildirim/{index}"


def _windows(since: date, until: date) -> list[tuple[date, date]]:
    windows = []
    start = since
    while start <= until:
        end = min(start + timedelta(days=MAX_WINDOW_DAYS), until)
        windows.append((start, end))
        start = end + timedelta(days=1)
    return windows


def _criteria(member_oid: str, start: date, end: date) -> dict[str, Any]:
    return {
        "fromDate": start.isoformat(),
        "toDate": end.isoformat(),
        "memberType": "",
        "mkkMemberOidList": [member_oid],
        "inactiveMkkMemberOidList": [],
        "disclosureClass": "",
        "subjectList": [],
        "isLate": "",
        "mainSector": "",
        "sector": "",
        "subSector": "",
        "marketOid": "",
        "index": "",
        "bdkReview": "",
        "bdkMemberOidList": [],
        "year": "",
        "term": "",
        "ruleType": "",
        "period": "",
        "fromSrc": False,
        "srcCategory": "",
        "disclosureIndexList": [],
    }


def _turkish_pdf(attachments: list[dict[str, Any]]) -> dict[str, Any] | None:
    pdfs = [a for a in attachments if str(a.get("fileExtension", "")).lower() == "pdf"]
    for attachment in pdfs:
        # Python lowercases "İ" to "i" + combining dot; drop the dot so "İng" matches "ing".
        name = f" {str(attachment.get('fileName', '')).lower().replace(chr(0x307), '')} "
        if not any(marker in name for marker in _ENGLISH_MARKERS):
            return attachment
    return pdfs[0] if pdfs else None


class KapSource:
    """Lists in-scope disclosures of one BIST company and fetches their content.

    `fetch` returns the PDF attachment for annual/interim reports and prospectuses, and the
    `attachment-detail` JSON (rendered body) for financial reports and material events.
    """

    def __init__(
        self,
        client: RateLimitedClient,
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
    ) -> None:
        self._client = client
        self._today = today

    async def list_documents(self, company: Company, since: datetime) -> list[RemoteDocument]:
        if company.kap_member_id is None:
            return []
        documents: dict[str, RemoteDocument] = {}
        for start, end in _windows(since.date(), self._today()):
            response = await self._client.post_json(
                f"{KAP_BASE}/api/disclosure/members/byCriteria",
                _criteria(company.kap_member_id, start, end),
            )
            items = response.json()
            if not isinstance(items, list):
                raise ValueError("unexpected KAP listing payload")
            for item in items:
                doc_type = classify(item)
                if doc_type is None:
                    continue
                published = parse_publish_date(item["publishDate"])
                if published < since:
                    continue
                index = str(item["disclosureIndex"])
                wants_pdf = doc_type in (DocType.KAP_ANNUAL_REPORT, DocType.KAP_PROSPECTUS)
                documents[index] = RemoteDocument(
                    ticker=company.ticker,
                    source_url=disclosure_url(index),
                    doc_type=doc_type,
                    period=period_of(item, published),
                    published_at=published,
                    language=Language.TR,
                    extra={
                        "disclosure_index": index,
                        "format": "pdf" if wants_pdf else "kap_json",
                        "subject": str(item.get("subject") or ""),
                    },
                )
        return sorted(documents.values(), key=lambda d: d.published_at)

    async def fetch(self, remote: RemoteDocument) -> bytes:
        index = remote.extra.get("disclosure_index") or remote.source_url.rsplit("/", 1)[-1]
        detail = await self._client.get(f"{KAP_BASE}/api/notification/attachment-detail/{index}")
        if remote.extra.get("format") != "pdf":
            return detail.content
        payload = json.loads(detail.content)
        entry = payload[0] if isinstance(payload, list) and payload else {}
        attachment = _turkish_pdf(list(entry.get("attachments") or []))
        if attachment is None:
            raise ValueError("disclosure has no PDF attachment")
        response = await self._client.get(f"{KAP_BASE}/api/file/download/{attachment['objId']}")
        return response.content
