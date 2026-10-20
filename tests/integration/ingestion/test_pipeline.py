"""Pipeline against a real Postgres with fixture sources (US1 independent test)."""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, func, select, text

from ingestion import schema
from ingestion.chunk import ChunkerConfig, WhitespaceCounter
from ingestion.config import Company
from ingestion.facts import FactDraft, extract_sec_facts
from ingestion.models import DocType
from ingestion.parse.types import ParsedDocument
from ingestion.pipeline import Pipeline, SourceAdapter
from ingestion.sources.base import RemoteDocument
from tests.integration.ingestion.fakes import (
    AAPL,
    FIXTURES,
    KAP_URL,
    SEC_URL,
    THYAO,
    FakeSource,
    kap_remote,
    sec_remote,
)

pytestmark = pytest.mark.integration

SINCE = datetime(2024, 1, 1, tzinfo=UTC)
COUNTED_TABLES = (
    schema.companies,
    schema.documents,
    schema.sections,
    schema.tables,
    schema.chunks,
    schema.financial_facts,
)


async def _sec_facts(company: Company, accessions: Mapping[str, int]) -> list[FactDraft]:
    raw = json.loads((FIXTURES / "sec" / "companyfacts_CIK0000320193.json").read_text())
    return extract_sec_facts(raw, accessions)


def _sources() -> tuple[FakeSource, FakeSource]:
    sec = FakeSource(
        "AAPL", [sec_remote()], {SEC_URL: (FIXTURES / "sec" / "aapl-10k-trimmed.htm").read_bytes()}
    )
    kap = FakeSource(
        "THYAO", [kap_remote()], {KAP_URL: (FIXTURES / "pdf" / "text-report.pdf").read_bytes()}
    )
    return sec, kap


def _pipeline(
    engine: Engine,
    sec: FakeSource,
    kap: FakeSource,
    tmp_path: Path,
    *,
    size_bytes: int = 0,
    kap_parser: Any = None,
) -> Pipeline:
    adapters = [
        SourceAdapter("sec", sec, frozenset({"us"}), company_facts=_sec_facts),
        SourceAdapter("kap", kap, frozenset({"bist"})),
    ]
    if kap_parser is not None:
        adapters[1].parser = kap_parser
    return Pipeline(
        engine,
        adapters,
        data_dir=tmp_path / "raw",
        disk_limit_gb=1,
        chunker=ChunkerConfig(size=60, overlap=8),
        counter=WhitespaceCounter(),
        size_bytes=lambda: size_bytes,
    )


def _snapshot(engine: Engine) -> dict[str, Any]:
    with engine.connect() as conn:
        counts = {
            t.name: conn.execute(select(func.count()).select_from(t)).scalar_one()
            for t in COUNTED_TABLES
        }
        docs = conn.execute(
            select(
                schema.documents.c.id, schema.documents.c.status, schema.documents.c.attempts
            ).order_by(schema.documents.c.id)
        ).all()
        chunk_ids = (
            conn.execute(select(schema.chunks.c.id).order_by(schema.chunks.c.id)).scalars().all()
        )
    return {"counts": counts, "docs": [tuple(d) for d in docs], "chunks": list(chunk_ids)}


async def test_idempotent_second_run(engine: Engine, tmp_path: Path) -> None:
    sec, kap = _sources()
    pipeline = _pipeline(engine, sec, kap, tmp_path)

    first = await pipeline.run([AAPL, THYAO], SINCE)
    assert (first.fetched, first.skipped, first.failed) == (2, 0, 0)
    before = _snapshot(engine)

    second = await pipeline.run([AAPL, THYAO], SINCE)
    assert (second.fetched, second.skipped, second.failed) == (0, 2, 0)
    assert _snapshot(engine) == before  # 0 rows created, 0 rows changed

    with engine.connect() as conn:
        runs = conn.execute(
            select(schema.ingestion_runs).order_by(schema.ingestion_runs.c.id)
        ).all()
    assert [(r.fetched, r.skipped, r.failed) for r in runs] == [(2, 0, 0), (0, 2, 0)]
    assert all(r.finished_at is not None for r in runs)


async def test_every_chunk_and_fact_has_metadata_and_source(engine: Engine, tmp_path: Path) -> None:
    sec, kap = _sources()
    await _pipeline(engine, sec, kap, tmp_path).run([AAPL, THYAO], SINCE)
    with engine.connect() as conn:
        missing = conn.execute(
            text(
                "SELECT count(*) FROM chunks WHERE ticker IS NULL OR period IS NULL "
                "OR doc_type IS NULL OR section_id IS NULL OR language IS NULL"
            )
        ).scalar_one()
        facts = conn.execute(select(schema.financial_facts)).all()
        statuses = conn.execute(select(schema.documents.c.status)).scalars().all()
        languages: dict[str, str] = dict(
            conn.execute(
                text("SELECT ticker, min(language::text) FROM chunks GROUP BY ticker")
            ).all()
        )
        tables = conn.execute(select(func.count()).select_from(schema.tables)).scalar_one()
    assert missing == 0
    assert facts and all(f.source_document_id and f.source_ref for f in facts)
    assert {f.metric for f in facts} >= {"revenue", "net_income", "ebitda"}
    assert set(statuses) == {"chunked"}
    assert languages == {"AAPL": "en", "THYAO": "tr"}
    assert tables == 2


async def test_raw_files_removed(engine: Engine, tmp_path: Path) -> None:
    sec, kap = _sources()
    await _pipeline(engine, sec, kap, tmp_path).run([AAPL, THYAO], SINCE)
    assert list((tmp_path / "raw").iterdir()) == []


async def test_failure_isolated_and_retried(engine: Engine, tmp_path: Path) -> None:
    sec, kap = _sources()

    def broken(remote: RemoteDocument, content: bytes) -> ParsedDocument:
        raise ValueError("cannot parse")

    first = await _pipeline(engine, sec, kap, tmp_path, kap_parser=broken).run([AAPL, THYAO], SINCE)
    assert (first.fetched, first.failed) == (1, 1)
    with engine.connect() as conn:
        failed = conn.execute(
            select(schema.documents).where(schema.documents.c.status == "failed")
        ).one()
    assert failed.failure_reason == "ValueError: cannot parse"
    assert failed.attempts == 1
    assert list((tmp_path / "raw").iterdir()) == []

    second = await _pipeline(engine, sec, kap, tmp_path).run([AAPL, THYAO], SINCE)
    assert (second.fetched, second.skipped, second.failed) == (1, 1, 0)
    with engine.connect() as conn:
        row = conn.execute(select(schema.documents).where(schema.documents.c.id == failed.id)).one()
    assert (row.status, row.failure_reason, row.attempts) == ("chunked", None, 1)


async def test_fetch_error_does_not_stop_run(engine: Engine, tmp_path: Path) -> None:
    sec, kap = _sources()
    sec.fail_urls.add(SEC_URL)
    summary = await _pipeline(engine, sec, kap, tmp_path).run([AAPL, THYAO], SINCE)
    assert (summary.fetched, summary.failed) == (1, 1)


async def test_interrupted_run_resumes(engine: Engine, tmp_path: Path) -> None:
    sec, kap = _sources()
    pipeline = _pipeline(engine, sec, kap, tmp_path)
    await pipeline.run([AAPL], SINCE)
    # Simulate a crash after fetch: the row stays in `fetched` with partial chunks removed.
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM chunks"))
        conn.execute(text("UPDATE documents SET status = 'fetched'"))
    summary = await pipeline.run([AAPL], SINCE)
    assert (summary.fetched, summary.skipped) == (1, 0)
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(schema.chunks)).scalar_one() > 0
        assert conn.execute(select(func.count()).select_from(schema.documents)).scalar_one() == 1
        assert conn.execute(select(func.count()).select_from(schema.sections)).scalar_one() == 6


async def test_new_hash_at_known_url_supersedes(engine: Engine, tmp_path: Path) -> None:
    sec, kap = _sources()
    pipeline = _pipeline(engine, sec, kap, tmp_path)
    await pipeline.run([THYAO], SINCE)
    kap.bodies[KAP_URL] = (FIXTURES / "pdf" / "text-report.pdf").read_bytes() + b"\n% amended\n"
    summary = await pipeline.run([THYAO], SINCE)
    assert summary.fetched == 1
    with engine.connect() as conn:
        rows = conn.execute(select(schema.documents).order_by(schema.documents.c.id)).all()
    assert len(rows) == 2
    assert rows[0].supersedes_id is None
    assert rows[1].supersedes_id == rows[0].id
    assert rows[0].content_hash != rows[1].content_hash


async def test_disk_guard_refuses_fetches(engine: Engine, tmp_path: Path) -> None:
    sec, kap = _sources()
    summary = await _pipeline(engine, sec, kap, tmp_path, size_bytes=2 * 1024**3).run(
        [AAPL, THYAO], SINCE
    )
    assert summary.notes == {"disk_limit_reached": True}
    assert sec.fetches == [] and kap.fetches == []
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(schema.documents)).scalar_one() == 0
        run = conn.execute(select(schema.ingestion_runs)).one()
    assert run.notes == {"disk_limit_reached": True}


async def test_dry_run_writes_nothing(engine: Engine, tmp_path: Path) -> None:
    sec, kap = _sources()
    summary = await _pipeline(engine, sec, kap, tmp_path).run([AAPL, THYAO], SINCE, dry_run=True)
    assert summary.run_id is None and summary.listed == 2
    assert sec.fetches == []
    assert _snapshot(engine)["counts"]["documents"] == 0


async def test_rechunk_adds_new_version(engine: Engine, tmp_path: Path) -> None:
    sec, kap = _sources()
    pipeline = _pipeline(engine, sec, kap, tmp_path)
    await pipeline.run([AAPL, THYAO], SINCE)
    result = await pipeline.rechunk(ChunkerConfig(size=30, overlap=4))
    assert (result.documents, result.failed, result.changed) == (2, 0, 0)
    again = await pipeline.rechunk(ChunkerConfig(size=30, overlap=4))
    assert again.chunks == 0  # same IDs, nothing new
    with engine.connect() as conn:
        versions: dict[str, int] = dict(
            conn.execute(
                text("SELECT chunker_version, count(*) FROM chunks GROUP BY chunker_version")
            ).all()
        )
    assert set(versions) == {"v1-60-8", "v1-30-4"}
    assert versions["v1-30-4"] == result.chunks

    kap.bodies[KAP_URL] = b"%PDF changed"
    changed = await pipeline.rechunk(ChunkerConfig(size=40, overlap=4))
    assert changed.changed == 1 and changed.documents == 1


async def test_status_report(engine: Engine, tmp_path: Path) -> None:
    from ingestion.run import status_report

    sec, kap = _sources()
    await _pipeline(engine, sec, kap, tmp_path).run([AAPL, THYAO], SINCE)
    report = status_report(engine)
    assert report["documents"] == {"chunked": 2}
    assert report["chunks_missing_metadata"] == 0
    assert report["facts_missing_source"] == 0
    assert report["chunks"] == report["chunks_without_embedding"] > 0
    assert report["disk_bytes"] > 0
    assert report["last_run"]["fetched"] == 2


async def test_kap_financial_report_facts(engine: Engine, tmp_path: Path) -> None:
    from dataclasses import replace

    from ingestion.adapters import kap_document_facts
    from ingestion.models import DocType

    url = "https://www.kap.org.tr/tr/Bildirim/1512543"
    remote = replace(
        kap_remote(url),
        doc_type=DocType.KAP_FINANCIAL_REPORT,
        period="2025-Q3",
        extra={"format": "kap_json", "disclosure_index": "1512543"},
    )
    body = (FIXTURES / "kap" / "detail_financial_report.json").read_bytes()
    kap = FakeSource("THYAO", [remote], {url: body})
    sec, _ = _sources()
    pipeline = _pipeline(engine, sec, kap, tmp_path)
    pipeline._adapters[1].document_facts = kap_document_facts
    summary = await pipeline.run([THYAO], SINCE)
    assert summary.fetched == 1
    with engine.connect() as conn:
        facts = {
            (r.metric, r.period): r for r in conn.execute(select(schema.financial_facts)).all()
        }
        sections = conn.execute(select(func.count()).select_from(schema.sections)).scalar_one()
    assert sections == 3
    assert facts[("revenue", "2025-Q3")].currency == "TRY"
    assert "Hasılat" in facts[("revenue", "2025-Q3")].source_ref


async def test_rechunk_replace_rebuilds_selected_types(engine: Engine, tmp_path: Path) -> None:
    sec, kap = _sources()
    pipeline = _pipeline(engine, sec, kap, tmp_path)
    await pipeline.run([AAPL, THYAO], SINCE)
    with engine.begin() as conn:
        conn.execute(text("UPDATE chunks SET text = 'stale'"))
        conn.execute(text("UPDATE documents SET status = 'embedded'"))
    with engine.connect() as conn:
        kap_doc_type = conn.execute(
            text("SELECT doc_type FROM documents WHERE source = 'kap'")
        ).scalar_one()

    result = await pipeline.rechunk(
        ChunkerConfig(size=60, overlap=8), doc_types=[DocType(kap_doc_type)], replace=True
    )
    assert (result.documents, result.failed) == (1, 0) and result.chunks > 0
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT d.source, d.status, bool_or(c.text = 'stale') AS stale "
                "FROM documents d JOIN chunks c ON c.document_id = d.id GROUP BY 1, 2"
            )
        ).all()
    by_source = {r.source: (r.status, r.stale) for r in rows}
    assert by_source["kap"] == ("chunked", False)  # rebuilt, waits for new vectors
    assert by_source["sec"] == ("embedded", True)  # other types untouched


async def test_rechunk_fetches_and_parses_like_ingestion(engine: Engine, tmp_path: Path) -> None:
    from ingestion.pipeline import default_parser

    sec, kap = _sources()
    pipeline = _pipeline(engine, sec, kap, tmp_path)
    await pipeline.run([AAPL, THYAO], SINCE)
    seen: dict[str, dict[str, str]] = {}

    def recording(remote: RemoteDocument, content: bytes) -> ParsedDocument:
        seen[remote.source_url] = dict(remote.extra)
        return default_parser(remote, content)

    for adapter in pipeline._adapters:
        adapter.parser = recording
    await pipeline.rechunk(ChunkerConfig(size=30, overlap=4))
    assert seen[SEC_URL] == {"format": "html"}
    # The fixture KAP document is an annual report: its content is the PDF attachment.
    assert seen[KAP_URL]["format"] == "pdf"
    assert seen[KAP_URL]["disclosure_index"] == KAP_URL.rsplit("/", 1)[-1]
