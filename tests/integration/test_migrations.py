"""Alembic migrations run against a real Postgres (CI service container or `make up`)."""

import os
from collections.abc import Iterator

import psycopg
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from api.config import get_settings
from api.schema import API_TABLES
from api.schema import metadata as api_metadata
from ingestion.schema import metadata

pytestmark = pytest.mark.integration

LOCAL_DATABASE_URL = "postgresql://disclosly:CHANGE_ME@localhost:5432/disclosly"


@pytest.fixture
def database_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    url = os.environ.get("DATABASE_URL", LOCAL_DATABASE_URL)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("REDIS_URL", os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://disclosly.com")
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


def _current_revision(url: str) -> str | None:
    with psycopg.connect(url) as conn:
        exists = conn.execute("SELECT to_regclass('public.alembic_version')").fetchone()
        if exists is None or exists[0] is None:
            return None
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return None if row is None else str(row[0])


def test_upgrade_head_then_downgrade_base(database_url: str) -> None:
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    assert _current_revision(database_url) == "0003"

    command.downgrade(config, "base")
    assert _current_revision(database_url) is None


CORPUS_TABLES = {
    "companies",
    "documents",
    "sections",
    "tables",
    "chunks",
    "financial_facts",
    "ingestion_runs",
}


@pytest.fixture
def migrated(database_url: str) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    config = Config("alembic.ini")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    with psycopg.connect(database_url, autocommit=True) as conn:
        yield conn
    command.downgrade(config, "base")


def _seed_document(conn: psycopg.Connection[tuple[object, ...]]) -> tuple[int, int]:
    company = conn.execute(
        "INSERT INTO companies (ticker, name, market, sec_cik) "
        "VALUES ('AAPL', 'Apple', 'us', '0000320193') RETURNING id"
    ).fetchone()
    assert company is not None
    document = conn.execute(
        "INSERT INTO documents (company_id, source, doc_type, period, published_at, source_url,"
        " content_hash, language, status) VALUES (%s, 'sec', '10-K', '2025-FY', now(),"
        " 'https://sec.gov/a', repeat('a', 64), 'en', 'fetched') RETURNING id",
        (company[0],),
    ).fetchone()
    assert document is not None
    return int(str(company[0])), int(str(document[0]))


def test_corpus_schema_matches_metadata(
    migrated: psycopg.Connection[tuple[object, ...]], database_url: str
) -> None:
    tables = {
        str(row[0])
        for row in migrated.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    }
    assert tables >= CORPUS_TABLES
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"include_name": lambda name, kind, _: kind != "table" or name in CORPUS_TABLES},
        )
        diff = compare_metadata(context, metadata)
    engine.dispose()
    assert diff == []


def test_text_search_configs_strip_accents(
    migrated: psycopg.Connection[tuple[object, ...]],
) -> None:
    row = migrated.execute(
        "SELECT to_tsvector('tr_unaccent', 'Şirketin gelirleri') "
        "@@ to_tsquery('tr_unaccent', 'sirket')"
    ).fetchone()
    assert row == (True,)


def test_documents_unique_source_url_content_hash(
    migrated: psycopg.Connection[tuple[object, ...]],
) -> None:
    company_id, _ = _seed_document(migrated)
    with pytest.raises(psycopg.errors.UniqueViolation):
        migrated.execute(
            "INSERT INTO documents (company_id, source, doc_type, period, published_at,"
            " source_url, content_hash, language, status) VALUES (%s, 'sec', '10-K', '2025-FY',"
            " now(), 'https://sec.gov/a', repeat('a', 64), 'en', 'fetched')",
            (company_id,),
        )


def test_failed_document_needs_reason(migrated: psycopg.Connection[tuple[object, ...]]) -> None:
    _, document_id = _seed_document(migrated)
    with pytest.raises(psycopg.errors.CheckViolation):
        migrated.execute("UPDATE documents SET status = 'failed' WHERE id = %s", (document_id,))
    migrated.execute(
        "UPDATE documents SET status = 'failed', failure_reason = 'parse error' WHERE id = %s",
        (document_id,),
    )


def test_company_market_ids_checked(migrated: psycopg.Connection[tuple[object, ...]]) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        migrated.execute(
            "INSERT INTO companies (ticker, name, market) VALUES ('THYAO', 'THY', 'bist')"
        )


def test_financial_facts_unique(migrated: psycopg.Connection[tuple[object, ...]]) -> None:
    company_id, document_id = _seed_document(migrated)
    insert = (
        "INSERT INTO financial_facts (company_id, metric, period, value, unit, currency,"
        " source_document_id, source_ref) VALUES (%s, 'revenue', '2025-FY', 1, 'USD', 'USD',"
        " %s, 'us-gaap:Revenues 0000320193-25-000079')"
    )
    migrated.execute(insert, (company_id, document_id))
    with pytest.raises(psycopg.errors.UniqueViolation):
        migrated.execute(insert, (company_id, document_id))


def test_chunk_tsv_generated_by_language(
    migrated: psycopg.Connection[tuple[object, ...]],
) -> None:
    company_id, document_id = _seed_document(migrated)
    migrated.execute(
        "INSERT INTO chunks (id, document_id, chunker_version, ordinal, kind, text, token_count,"
        " company_id, ticker, period, doc_type, language, embedding) VALUES (repeat('c', 64),"
        " %s, 'v1-600-64', 0, 'text', 'Revenues increased', 2, %s, 'AAPL', '2025-FY', '10-K',"
        " 'en', %s)",
        (document_id, company_id, "[" + ",".join(["0.1"] * 384) + "]"),
    )
    row = migrated.execute(
        "SELECT tsv @@ to_tsquery('en_unaccent', 'revenue') FROM chunks"
    ).fetchone()
    assert row == (True,)


def test_api_schema_matches_metadata(
    migrated: psycopg.Connection[tuple[object, ...]], database_url: str
) -> None:
    engine = create_engine(database_url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"include_name": lambda name, kind, _: kind != "table" or name in API_TABLES},
        )
        diff = compare_metadata(context, api_metadata)
    engine.dispose()
    assert diff == []


def test_downgrade_0003_drops_api_tables_only(database_url: str) -> None:
    config = Config("alembic.ini")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.downgrade(config, "0002")
    with psycopg.connect(database_url) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ).fetchall()
        }
        types = {
            str(row[0])
            for row in conn.execute(
                "SELECT typname FROM pg_type WHERE typname IN ('auth_provider', 'run_status')"
            ).fetchall()
        }
    assert tables.isdisjoint(API_TABLES)
    assert tables >= CORPUS_TABLES
    assert types == set()
    command.downgrade(config, "base")


def _seed_user(conn: psycopg.Connection[tuple[object, ...]], email: str = "a@example.com") -> str:
    row = conn.execute(
        "INSERT INTO users (email, display_name, auth_provider, provider_subject)"
        " VALUES (%s, 'Ayşe Öztürk 🚀', 'google', %s) RETURNING id, language",
        (email, email),
    ).fetchone()
    assert row is not None
    assert row[1] == "tr"
    return str(row[0])


def test_users_constraints(migrated: psycopg.Connection[tuple[object, ...]]) -> None:
    _seed_user(migrated)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _seed_user(migrated)
    with pytest.raises(psycopg.errors.CheckViolation):
        _seed_user(migrated, "Upper@Example.com")
    with pytest.raises(psycopg.errors.CheckViolation):
        migrated.execute(
            "INSERT INTO users (email, display_name, auth_provider, provider_subject)"
            " VALUES ('b@example.com', repeat('x', 201), 'github', 'b')"
        )
    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        migrated.execute(
            "INSERT INTO users (email, display_name, auth_provider, provider_subject)"
            " VALUES ('c@example.com', 'c', 'twitter', 'c')"
        )


def test_users_unique_provider_subject(migrated: psycopg.Connection[tuple[object, ...]]) -> None:
    insert = (
        "INSERT INTO users (email, display_name, auth_provider, provider_subject)"
        " VALUES (%s, 'x', 'github', 'same-subject')"
    )
    migrated.execute(insert, ("one@example.com",))
    with pytest.raises(psycopg.errors.UniqueViolation):
        migrated.execute(insert, ("two@example.com",))


def test_allowlist_email_lower(migrated: psycopg.Connection[tuple[object, ...]]) -> None:
    migrated.execute("INSERT INTO beta_allowlist (email) VALUES ('a@example.com')")
    with pytest.raises(psycopg.errors.CheckViolation):
        migrated.execute("INSERT INTO beta_allowlist (email) VALUES ('B@example.com')")


def test_runs_and_citations_constraints(
    migrated: psycopg.Connection[tuple[object, ...]],
) -> None:
    user_id = _seed_user(migrated)
    run = migrated.execute(
        "INSERT INTO runs (user_id, question, language) VALUES (%s, 'THYAO net kâr?', 'tr')"
        " RETURNING id, status, usage",
        (user_id,),
    ).fetchone()
    assert run is not None
    assert run[1] == "running"
    assert run[2] == {}
    for question in ("ab", "x" * 1001):
        with pytest.raises(psycopg.errors.CheckViolation):
            migrated.execute(
                "INSERT INTO runs (user_id, question, language) VALUES (%s, %s, 'en')",
                (user_id, question),
            )
    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        migrated.execute(
            "INSERT INTO runs (user_id, question, language, status)"
            " VALUES (%s, 'abc', 'en', 'paused')",
            (user_id,),
        )

    insert = (
        "INSERT INTO run_citations (run_id, cite_id, excerpt, source) VALUES (%s, %s, %s, '{}')"
    )
    migrated.execute(insert, (run[0], "c:abc", "x" * 1500))
    with pytest.raises(psycopg.errors.UniqueViolation):
        migrated.execute(insert, (run[0], "c:abc", "y"))
    with pytest.raises(psycopg.errors.CheckViolation):
        migrated.execute(insert, (run[0], "c:long", "x" * 1501))
    with pytest.raises(psycopg.errors.CheckViolation):
        migrated.execute(insert, (run[0], "z:1", "x"))

    migrated.execute("DELETE FROM users WHERE id = %s", (user_id,))
    remaining = migrated.execute("SELECT count(*) FROM run_citations").fetchone()
    assert remaining == (0,)


def test_llm_cache_defaults(migrated: psycopg.Connection[tuple[object, ...]]) -> None:
    row = migrated.execute(
        "INSERT INTO llm_cache (key, provider, model, response)"
        " VALUES (repeat('a', 64), 'groq-llama', 'm', '{}') RETURNING hits, last_hit_at"
    ).fetchone()
    assert row is not None
    assert row[0] == 0
    assert row[1] is not None
