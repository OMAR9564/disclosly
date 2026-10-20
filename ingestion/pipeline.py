"""Idempotent ingestion: fetch, hash, parse, chunk, facts, clean up (FR-003 to FR-011).

A document is skipped when `(source_url, content_hash)` already exists and reached `chunked`
or `embedded`. Rows left in `fetched`/`parsed` by an interrupted run and rows in `failed` are
processed again. A new hash at a known URL creates a new row that supersedes the old one.
Document text is untrusted data: it is stored as-is and never interpreted.
"""

import hashlib
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import Connection, Engine, delete, insert, or_, select, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ingestion import schema
from ingestion.chunk import ChunkDraft, ChunkerConfig, TokenCounter, chunk_document
from ingestion.config import Company
from ingestion.db import database_size_bytes
from ingestion.facts import FactDraft
from ingestion.models import DocType, DocumentStatus, Language
from ingestion.parse.html import parse_html
from ingestion.parse.kap import parse_kap_detail
from ingestion.parse.pdf import parse_pdf
from ingestion.parse.types import ParsedDocument
from ingestion.sources.base import RemoteDocument, Source
from ingestion.sources.kap import stored_extra as kap_stored_extra

logger = logging.getLogger("ingestion.pipeline")

SourceName = Literal["kap", "sec"]
Trigger = Literal["manual", "schedule"]
DONE_STATUSES = (DocumentStatus.CHUNKED.value, DocumentStatus.EMBEDDED.value)
MAX_REASON_CHARS = 500
GB = 1024**3

Parser = Callable[[RemoteDocument, bytes], ParsedDocument]
DocumentFacts = Callable[[RemoteDocument, ParsedDocument, int], list[FactDraft]]
# Receives the company and a map of accession number -> document id for its stored filings.
CompanyFacts = Callable[[Company, Mapping[str, int]], Awaitable[list[FactDraft]]]


def default_parser(remote: RemoteDocument, content: bytes) -> ParsedDocument:
    if content.startswith(b"%PDF") or remote.extra.get("format") == "pdf":
        return parse_pdf(content)
    if remote.extra.get("format") == "kap_json":
        return parse_kap_detail(content)
    return parse_html(content)


@dataclass
class SourceAdapter:
    name: SourceName
    source: Source
    markets: frozenset[str]
    parser: Parser = default_parser
    document_facts: DocumentFacts | None = None
    company_facts: CompanyFacts | None = None


@dataclass
class RunSummary:
    run_id: int | None
    fetched: int = 0
    skipped: int = 0
    failed: int = 0
    listed: int = 0
    duration_s: float = 0.0
    notes: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "fetched": self.fetched,
            "skipped": self.skipped,
            "failed": self.failed,
            "duration_s": round(self.duration_s, 1),
        }


@dataclass
class RechunkSummary:
    chunker_version: str
    documents: int = 0
    chunks: int = 0
    skipped: int = 0
    changed: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunker_version": self.chunker_version,
            "documents": self.documents,
            "chunks": self.chunks,
            "skipped": self.skipped,
            "changed": self.changed,
            "failed": self.failed,
        }


class DiskLimitReached(Exception):
    pass


class Pipeline:
    def __init__(
        self,
        engine: Engine,
        adapters: Sequence[SourceAdapter],
        *,
        data_dir: Path,
        disk_limit_gb: int,
        chunker: ChunkerConfig,
        counter: TokenCounter,
        size_bytes: Callable[[], int] | None = None,
    ) -> None:
        self._engine = engine
        self._adapters = list(adapters)
        self._data_dir = data_dir
        self._disk_limit_bytes = disk_limit_gb * GB
        self._chunker = chunker
        self._counter = counter
        self._size_bytes = size_bytes or (lambda: database_size_bytes(engine))

    async def run(
        self,
        companies: Sequence[Company],
        since: datetime,
        *,
        trigger: Trigger = "manual",
        dry_run: bool = False,
    ) -> RunSummary:
        started = time.monotonic()
        summary = RunSummary(run_id=None)
        if not dry_run:
            with self._engine.begin() as conn:
                summary.run_id = conn.execute(
                    insert(schema.ingestion_runs)
                    .values(trigger=trigger)
                    .returning(schema.ingestion_runs.c.id)
                ).scalar_one()
        try:
            for company in companies:
                for adapter in self._adapters:
                    if company.market in adapter.markets:
                        await self._company(adapter, company, since, summary, dry_run)
        except DiskLimitReached:
            summary.notes["disk_limit_reached"] = True
            logger.warning("disk limit reached, fetches stopped", extra={"run_id": summary.run_id})
        finally:
            summary.duration_s = time.monotonic() - started
            if summary.run_id is not None:
                with self._engine.begin() as conn:
                    conn.execute(
                        update(schema.ingestion_runs)
                        .where(schema.ingestion_runs.c.id == summary.run_id)
                        .values(
                            finished_at=datetime.now(UTC),
                            fetched=summary.fetched,
                            skipped=summary.skipped,
                            failed=summary.failed,
                            notes=summary.notes,
                        )
                    )
        logger.info("ingestion run finished", extra=summary.as_dict())
        return summary

    async def _company(
        self,
        adapter: SourceAdapter,
        company: Company,
        since: datetime,
        summary: RunSummary,
        dry_run: bool,
    ) -> None:
        try:
            remotes = await adapter.source.list_documents(company, since)
        except Exception as exc:
            summary.failed += 1
            summary.notes.setdefault("list_errors", []).append(
                {"ticker": company.ticker, "source": adapter.name, "error": _reason(exc)}
            )
            logger.warning(
                "listing failed",
                extra={"ticker": company.ticker, "source": adapter.name, "error": _reason(exc)},
            )
            return
        summary.listed += len(remotes)
        if dry_run:
            return
        company_id = self._upsert_company(company)
        for remote in remotes:
            await self._document(adapter, company_id, remote, summary)
        if adapter.company_facts is not None:
            await self._company_facts(adapter.company_facts, company, company_id, summary)

    async def _document(
        self, adapter: SourceAdapter, company_id: int, remote: RemoteDocument, summary: RunSummary
    ) -> None:
        if self._size_bytes() > self._disk_limit_bytes:
            raise DiskLimitReached
        log = {"ticker": remote.ticker, "source": adapter.name, "url": remote.source_url}
        try:
            content = await adapter.source.fetch(remote)
        except Exception as exc:
            summary.failed += 1
            logger.warning("fetch failed", extra={**log, "error": _reason(exc)})
            return
        content_hash = hashlib.sha256(content).hexdigest()
        document_id = self._claim_document(adapter.name, company_id, remote, content_hash)
        if document_id is None:
            summary.skipped += 1
            return
        raw_path = self._data_dir / f"{content_hash}.raw"
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(content)
            parsed = adapter.parser(remote, raw_path.read_bytes())
            facts = (
                adapter.document_facts(remote, parsed, document_id)
                if adapter.document_facts is not None
                else []
            )
            self._store(document_id, company_id, remote, parsed, facts)
        except Exception as exc:
            summary.failed += 1
            self._mark_failed(document_id, _reason(exc))
            logger.warning("document failed", extra={**log, "error": _reason(exc)})
        else:
            summary.fetched += 1
            logger.info("document stored", extra={**log, "document_id": document_id})
        finally:
            raw_path.unlink(missing_ok=True)

    def _upsert_company(self, company: Company) -> int:
        table = schema.companies
        values = {
            "ticker": company.ticker,
            "name": company.name,
            "market": company.market,
            "kap_member_id": company.kap_member_id,
            "sec_cik": company.sec_cik,
        }
        stmt = pg_insert(table).values(**values)
        changed = [table.c[k].is_distinct_from(stmt.excluded[k]) for k in values if k != "ticker"]
        with self._engine.begin() as conn:
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=[table.c.ticker],
                    set_={k: stmt.excluded[k] for k in values if k != "ticker"},
                    where=or_(*changed),
                )
            )
            return int(
                conn.execute(
                    select(table.c.id).where(table.c.ticker == company.ticker)
                ).scalar_one()
            )

    def _claim_document(
        self, source: SourceName, company_id: int, remote: RemoteDocument, content_hash: str
    ) -> int | None:
        """Return the document id to process, or None when it is already done."""
        docs = schema.documents
        with self._engine.begin() as conn:
            row = conn.execute(
                select(docs.c.id, docs.c.status).where(
                    docs.c.source_url == remote.source_url, docs.c.content_hash == content_hash
                )
            ).first()
            if row is not None:
                if row.status in DONE_STATUSES:
                    return None
                conn.execute(
                    update(docs)
                    .where(docs.c.id == row.id)
                    .values(status=DocumentStatus.FETCHED.value, failure_reason=None)
                )
                return int(row.id)
            previous = conn.execute(
                select(docs.c.id)
                .where(docs.c.source_url == remote.source_url)
                .order_by(docs.c.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            return int(
                conn.execute(
                    insert(docs)
                    .values(
                        company_id=company_id,
                        source=source,
                        doc_type=remote.doc_type.value,
                        period=remote.period,
                        published_at=remote.published_at,
                        source_url=remote.source_url,
                        content_hash=content_hash,
                        language=remote.language.value,
                        status=DocumentStatus.FETCHED.value,
                        supersedes_id=previous,
                        attempts=0,
                    )
                    .returning(docs.c.id)
                ).scalar_one()
            )

    def _store(
        self,
        document_id: int,
        company_id: int,
        remote: RemoteDocument,
        parsed: ParsedDocument,
        facts: list[FactDraft],
    ) -> None:
        drafts = chunk_document(document_id, parsed, self._chunker, self._counter)
        with self._engine.begin() as conn:
            _clear_document(conn, document_id, self._chunker.version)
            section_ids: list[int] = []
            for ordinal, section in enumerate(parsed.sections):
                section_id = conn.execute(
                    insert(schema.sections)
                    .values(
                        document_id=document_id,
                        ordinal=ordinal,
                        label=section.label,
                        title=section.title,
                        page_start=section.page_start,
                        page_end=section.page_end,
                    )
                    .returning(schema.sections.c.id)
                ).scalar_one()
                section_ids.append(int(section_id))
                for table in section.tables:
                    conn.execute(
                        insert(schema.tables).values(
                            document_id=document_id,
                            section_id=section_id,
                            page=table.page,
                            header=table.header,
                            rows=table.rows,
                        )
                    )
            conn.execute(
                update(schema.documents)
                .where(schema.documents.c.id == document_id)
                .values(status=DocumentStatus.PARSED.value)
            )
            meta = {
                "company_id": company_id,
                "ticker": remote.ticker,
                "period": remote.period,
                "doc_type": remote.doc_type.value,
                "language": remote.language.value,
            }
            insert_chunks(conn, document_id, self._chunker.version, drafts, section_ids, meta)
            insert_facts(conn, company_id, facts)
            conn.execute(
                update(schema.documents)
                .where(schema.documents.c.id == document_id)
                .values(status=DocumentStatus.CHUNKED.value)
            )

    def _mark_failed(self, document_id: int, reason: str) -> None:
        docs = schema.documents
        with self._engine.begin() as conn:
            conn.execute(
                update(docs)
                .where(docs.c.id == document_id)
                .values(
                    status=DocumentStatus.FAILED.value,
                    failure_reason=reason,
                    attempts=docs.c.attempts + 1,
                )
            )

    async def rechunk(
        self,
        chunker: ChunkerConfig,
        *,
        doc_types: Sequence[DocType] | None = None,
        document_ids: Sequence[int] | None = None,
        replace: bool = False,
    ) -> RechunkSummary:
        """Build chunks for another chunker version (sweeps, research §R4).

        Raw files are deleted after parsing, so each document is fetched again; a document
        whose bytes changed since ingestion is skipped (the next `ingest` supersedes it).
        With `replace`, existing chunks of this version are rebuilt (after a parser change);
        their vectors are recomputed by `ingestion.embed`.
        """
        docs = schema.documents
        summary = RechunkSummary(chunker_version=chunker.version)
        adapters = {a.name: a for a in self._adapters}
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(docs)
                .where(
                    docs.c.status.in_(DONE_STATUSES),
                    docs.c.doc_type.in_([d.value for d in doc_types]) if doc_types else true(),
                    docs.c.id.in_(list(document_ids)) if document_ids is not None else true(),
                )
                .order_by(docs.c.id)
            ).all()
        for row in rows:
            adapter = adapters.get(row.source)
            if adapter is None:
                summary.skipped += 1
                continue
            doc_type = DocType(row.doc_type)
            remote = RemoteDocument(
                ticker="",
                source_url=row.source_url,
                doc_type=doc_type,
                period=row.period,
                published_at=row.published_at,
                language=Language(row.language),
                # Fetch and parse the same way as at ingestion (PDF attachment vs KAP body).
                extra=kap_stored_extra(doc_type, row.source_url)
                if row.source == "kap"
                else {"format": "html"},
            )
            try:
                content = await adapter.source.fetch(remote)
                if hashlib.sha256(content).hexdigest() != row.content_hash:
                    summary.changed += 1
                    continue
                parsed = adapter.parser(remote, content)
                drafts = chunk_document(row.id, parsed, chunker, self._counter)
                with self._engine.begin() as conn:
                    section_ids = (
                        conn.execute(
                            select(schema.sections.c.id)
                            .where(schema.sections.c.document_id == row.id)
                            .order_by(schema.sections.c.ordinal)
                        )
                        .scalars()
                        .all()
                    )
                    if len(section_ids) != len(parsed.sections):
                        raise ValueError("section count differs from stored sections")
                    if replace:
                        chunks = schema.chunks
                        conn.execute(
                            delete(chunks).where(
                                chunks.c.document_id == row.id,
                                chunks.c.chunker_version == chunker.version,
                            )
                        )
                        conn.execute(
                            update(docs)
                            .where(
                                docs.c.id == row.id,
                                docs.c.status == DocumentStatus.EMBEDDED.value,
                            )
                            .values(status=DocumentStatus.CHUNKED.value)
                        )
                    ticker = conn.execute(
                        select(schema.companies.c.ticker).where(
                            schema.companies.c.id == row.company_id
                        )
                    ).scalar_one()
                    meta = {
                        "company_id": row.company_id,
                        "ticker": ticker,
                        "period": row.period,
                        "doc_type": row.doc_type,
                        "language": row.language,
                    }
                    summary.chunks += insert_chunks(
                        conn, row.id, chunker.version, drafts, section_ids, meta
                    )
                summary.documents += 1
            except Exception as exc:
                summary.failed += 1
                logger.warning(
                    "rechunk failed", extra={"document_id": row.id, "error": _reason(exc)}
                )
        logger.info("rechunk finished", extra=summary.as_dict())
        return summary

    async def _company_facts(
        self, provider: CompanyFacts, company: Company, company_id: int, summary: RunSummary
    ) -> None:
        docs = schema.documents
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(docs.c.id, docs.c.source_url).where(
                    docs.c.company_id == company_id, docs.c.status.in_(DONE_STATUSES)
                )
            ).all()
        accessions: dict[str, int] = {}
        for row in rows:
            accession = _accession(row.source_url)
            if accession is not None:
                accessions[accession] = int(row.id)
        if not accessions:
            return
        try:
            facts = await provider(company, accessions)
        except Exception as exc:
            summary.notes.setdefault("fact_errors", []).append(
                {"ticker": company.ticker, "error": _reason(exc)}
            )
            return
        with self._engine.begin() as conn:
            insert_facts(conn, company_id, facts)


def _accession(source_url: str) -> str | None:
    """SEC archive URLs hold the accession number without dashes as a path segment."""
    parts = source_url.split("/")
    if "edgar" not in parts or len(parts) < 2:
        return None
    raw = parts[-2]
    if len(raw) != 18 or not raw.isdigit():
        return None
    return f"{raw[:10]}-{raw[10:12]}-{raw[12:]}"


def _clear_document(conn: Connection, document_id: int, chunker_version: str) -> None:
    """Remove partial rows from an interrupted or failed attempt before storing again."""
    conn.execute(
        delete(schema.chunks).where(
            schema.chunks.c.document_id == document_id,
            schema.chunks.c.chunker_version == chunker_version,
        )
    )
    conn.execute(delete(schema.tables).where(schema.tables.c.document_id == document_id))
    conn.execute(
        delete(schema.financial_facts).where(
            schema.financial_facts.c.source_document_id == document_id
        )
    )
    conn.execute(delete(schema.sections).where(schema.sections.c.document_id == document_id))


def insert_chunks(
    conn: Connection,
    document_id: int,
    chunker_version: str,
    drafts: Sequence[ChunkDraft],
    section_ids: Sequence[int],
    meta: Mapping[str, Any],
) -> int:
    """Insert chunks with their denormalised filter metadata; existing IDs are kept."""
    if not drafts:
        return 0
    result = conn.execute(
        pg_insert(schema.chunks)
        .values(
            [
                {
                    "id": d.id,
                    "document_id": document_id,
                    "section_id": section_ids[d.section_index],
                    "chunker_version": chunker_version,
                    "ordinal": d.ordinal,
                    "kind": d.kind,
                    "text": d.text,
                    "token_count": d.token_count,
                    "page": d.page,
                    **meta,
                }
                for d in drafts
            ]
        )
        .on_conflict_do_nothing(index_elements=[schema.chunks.c.id])
        .returning(schema.chunks.c.id)
    )
    return len(result.all())


def insert_facts(conn: Connection, company_id: int, facts: Sequence[FactDraft]) -> None:
    if not facts:
        return
    conn.execute(
        pg_insert(schema.financial_facts)
        .values(
            [
                {
                    "company_id": company_id,
                    "metric": f.metric,
                    "period": f.period,
                    "value": f.value,
                    "unit": f.unit,
                    "currency": f.currency,
                    "source_document_id": f.source_document_id,
                    "source_ref": f.source_ref,
                    "derived": f.derived,
                }
                for f in facts
            ]
        )
        .on_conflict_do_nothing(constraint="uq_financial_facts_company_metric_period_document")
    )


def _reason(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:MAX_REASON_CHARS]
