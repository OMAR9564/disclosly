"""Wire configured sources into pipeline adapters (SEC for `us`, KAP for `bist`)."""

from collections.abc import Mapping
from contextlib import AsyncExitStack

from ingestion.config import Company, IngestionSettings
from ingestion.facts import FactDraft, extract_kap_facts, extract_sec_facts
from ingestion.models import DocType
from ingestion.parse.types import ParsedDocument
from ingestion.pipeline import SourceAdapter
from ingestion.sources.base import RemoteDocument
from ingestion.sources.kap import KapSource, kap_client
from ingestion.sources.sec import SecSource, sec_client


def sec_adapter(source: SecSource) -> SourceAdapter:
    async def company_facts(company: Company, accessions: Mapping[str, int]) -> list[FactDraft]:
        return extract_sec_facts(await source.company_facts(company), accessions)

    return SourceAdapter("sec", source, frozenset({"us"}), company_facts=company_facts)


def kap_document_facts(
    remote: RemoteDocument, parsed: ParsedDocument, document_id: int
) -> list[FactDraft]:
    if remote.doc_type is not DocType.KAP_FINANCIAL_REPORT:
        return []
    return extract_kap_facts(parsed, document_id)


def kap_adapter(source: KapSource) -> SourceAdapter:
    return SourceAdapter("kap", source, frozenset({"bist"}), document_facts=kap_document_facts)


async def build_adapters(
    settings: IngestionSettings, stack: AsyncExitStack, only: str | None = None
) -> list[SourceAdapter]:
    """HTTP clients are closed when `stack` exits."""
    adapters: list[SourceAdapter] = []
    if only in (None, "sec"):
        client = await stack.enter_async_context(sec_client(settings))
        adapters.append(sec_adapter(SecSource(client)))
    if only in (None, "kap"):
        client = await stack.enter_async_context(kap_client())
        adapters.append(kap_adapter(KapSource(client)))
    return adapters
