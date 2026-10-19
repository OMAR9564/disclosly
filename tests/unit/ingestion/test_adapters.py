from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ingestion.adapters import build_adapters, kap_document_facts, sec_adapter
from ingestion.config import Company, IngestionSettings
from ingestion.models import DocType, Language
from ingestion.parse.kap import parse_kap_detail
from ingestion.sources.base import RemoteDocument

FIXTURES = Path(__file__).parents[2] / "fixtures"


def _remote(doc_type: DocType) -> RemoteDocument:
    return RemoteDocument(
        "THYAO", "u", doc_type, "2025-Q3", datetime(2025, 11, 7, tzinfo=UTC), Language.TR
    )


async def test_build_adapters_by_source() -> None:
    settings = IngestionSettings(sec_user_agent="Disclosly ops@example.com")
    async with AsyncExitStack() as stack:
        both = await build_adapters(settings, stack)
        kap_only = await build_adapters(IngestionSettings(), stack, "kap")
    assert [(a.name, sorted(a.markets)) for a in both] == [("sec", ["us"]), ("kap", ["bist"])]
    assert [a.name for a in kap_only] == ["kap"]


async def test_sec_requires_user_agent() -> None:
    async with AsyncExitStack() as stack:
        with pytest.raises(RuntimeError, match="SEC_USER_AGENT"):
            await build_adapters(IngestionSettings(), stack, "sec")


def test_kap_facts_only_for_financial_reports() -> None:
    parsed = parse_kap_detail((FIXTURES / "kap" / "detail_financial_report.json").read_bytes())
    assert kap_document_facts(_remote(DocType.KAP_MATERIAL_EVENT), parsed, 1) == []
    assert kap_document_facts(_remote(DocType.KAP_FINANCIAL_REPORT), parsed, 1)


async def test_sec_company_facts_wrapper() -> None:
    class Source:
        async def company_facts(self, company: Company) -> dict[str, object]:
            return {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
                {"start": "2025-01-01", "end": "2025-12-31", "val": 1, "accn": "a"},
            ]}}}}}  # fmt: skip

    adapter = sec_adapter(Source())  # type: ignore[arg-type]
    assert adapter.company_facts is not None
    aapl = Company(ticker="AAPL", name="Apple", market="us", sec_cik="0000320193")
    facts = await adapter.company_facts(aapl, {"a": 9})
    assert [(f.metric, f.period, f.source_document_id) for f in facts] == [
        ("net_income", "2025-FY", 9)
    ]
