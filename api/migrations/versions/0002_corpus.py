"""Corpus schema: companies, documents, sections, tables, chunks, facts, ingestion runs.

Plain SQL so the migration stays frozen when `ingestion.schema` changes later.
`tests/integration/test_migrations.py` checks that both describe the same schema.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS_CONFIGS = {
    "tr_unaccent": ("turkish", "turkish_stem"),
    "en_unaccent": ("english", "english_stem"),
}

_ENUMS = {
    "market": ("bist", "us"),
    "document_source": ("kap", "sec"),
    "doc_type": (
        "10-K",
        "10-Q",
        "kap_financial_report",
        "kap_material_event",
        "kap_annual_report",
        "kap_prospectus",
        "kap_fund_report",
    ),
    "language": ("tr", "en"),
    "document_status": ("fetched", "parsed", "chunked", "embedded", "failed"),
    "chunk_kind": ("text", "table"),
    "run_trigger": ("manual", "schedule"),
}

_TABLES = (
    "chunks",
    "tables",
    "sections",
    "financial_facts",
    "documents",
    "ingestion_runs",
    "companies",
)

_DDL = [
    """
    CREATE TABLE companies (
        id BIGSERIAL PRIMARY KEY,
        ticker TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        market market NOT NULL,
        kap_member_id TEXT,
        sec_cik VARCHAR(10),
        CONSTRAINT ck_companies_ticker_upper CHECK (ticker = upper(ticker)),
        CONSTRAINT ck_companies_bist_kap_member_id
            CHECK (market <> 'bist' OR kap_member_id IS NOT NULL),
        CONSTRAINT ck_companies_us_sec_cik CHECK (market <> 'us' OR sec_cik ~ '^[0-9]{10}$')
    )
    """,
    """
    CREATE TABLE ingestion_runs (
        id BIGSERIAL PRIMARY KEY,
        started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
        finished_at TIMESTAMP WITH TIME ZONE,
        trigger run_trigger NOT NULL,
        fetched INTEGER NOT NULL DEFAULT '0',
        skipped INTEGER NOT NULL DEFAULT '0',
        failed INTEGER NOT NULL DEFAULT '0',
        notes JSONB NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE documents (
        id BIGSERIAL PRIMARY KEY,
        company_id BIGINT NOT NULL REFERENCES companies (id),
        source document_source NOT NULL,
        doc_type doc_type NOT NULL,
        period TEXT NOT NULL,
        published_at TIMESTAMP WITH TIME ZONE NOT NULL,
        source_url TEXT NOT NULL,
        content_hash CHAR(64) NOT NULL,
        language language NOT NULL,
        status document_status NOT NULL,
        failure_reason TEXT,
        supersedes_id BIGINT REFERENCES documents (id),
        attempts INTEGER NOT NULL DEFAULT '0',
        CONSTRAINT uq_documents_source_url_content_hash UNIQUE (source_url, content_hash),
        CONSTRAINT ck_documents_failure_reason
            CHECK ((status = 'failed') = (failure_reason IS NOT NULL))
    )
    """,
    "CREATE INDEX ix_documents_company_id ON documents (company_id)",
    """
    CREATE TABLE financial_facts (
        id BIGSERIAL PRIMARY KEY,
        company_id BIGINT NOT NULL REFERENCES companies (id),
        metric TEXT NOT NULL,
        period TEXT NOT NULL,
        value NUMERIC NOT NULL,
        unit TEXT NOT NULL,
        currency TEXT,
        source_document_id BIGINT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
        source_ref TEXT NOT NULL,
        derived BOOLEAN NOT NULL DEFAULT 'false',
        CONSTRAINT uq_financial_facts_company_metric_period_document
            UNIQUE (company_id, metric, period, source_document_id)
    )
    """,
    """
    CREATE TABLE sections (
        id BIGSERIAL PRIMARY KEY,
        document_id BIGINT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL,
        label TEXT NOT NULL,
        title TEXT,
        page_start INTEGER,
        page_end INTEGER,
        CONSTRAINT uq_sections_document_id_ordinal UNIQUE (document_id, ordinal)
    )
    """,
    """
    CREATE TABLE chunks (
        id CHAR(64) PRIMARY KEY,
        document_id BIGINT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
        section_id BIGINT REFERENCES sections (id) ON DELETE CASCADE,
        chunker_version TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        kind chunk_kind NOT NULL,
        text TEXT NOT NULL,
        token_count INTEGER NOT NULL,
        page INTEGER,
        company_id BIGINT NOT NULL REFERENCES companies (id),
        ticker TEXT NOT NULL,
        period TEXT NOT NULL,
        doc_type doc_type NOT NULL,
        language language NOT NULL,
        embedding VECTOR(384),
        tsv TSVECTOR GENERATED ALWAYS AS (
            to_tsvector(
                CASE WHEN language = 'tr' THEN 'tr_unaccent'::regconfig
                ELSE 'en_unaccent'::regconfig END,
                text
            )
        ) STORED,
        CONSTRAINT uq_chunks_document_version_ordinal
            UNIQUE (document_id, chunker_version, ordinal)
    )
    """,
    """
    CREATE INDEX ix_chunks_embedding_hnsw ON chunks
        USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)
    """,
    """
    CREATE INDEX ix_chunks_filters
        ON chunks (ticker, period, doc_type, language, chunker_version)
    """,
    "CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv)",
    """
    CREATE TABLE tables (
        id BIGSERIAL PRIMARY KEY,
        document_id BIGINT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
        section_id BIGINT REFERENCES sections (id) ON DELETE CASCADE,
        page INTEGER,
        header JSONB NOT NULL,
        rows JSONB NOT NULL
    )
    """,
    "CREATE INDEX ix_tables_document_id ON tables (document_id)",
]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    for name, (copy_from, stemmer) in _TS_CONFIGS.items():
        op.execute(f"CREATE TEXT SEARCH CONFIGURATION {name} (COPY = pg_catalog.{copy_from})")
        op.execute(
            f"ALTER TEXT SEARCH CONFIGURATION {name} "
            f"ALTER MAPPING FOR hword, hword_part, word WITH unaccent, {stemmer}"
        )
    for enum_name, values in _ENUMS.items():
        labels = ", ".join(f"'{value}'" for value in values)
        op.execute(f"CREATE TYPE {enum_name} AS ENUM ({labels})")
    for statement in _DDL:
        op.execute(statement)


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    for enum_name in _ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
    for name in _TS_CONFIGS:
        op.execute(f"DROP TEXT SEARCH CONFIGURATION IF EXISTS {name}")
    op.execute("DROP EXTENSION IF EXISTS unaccent")
    op.execute("DROP EXTENSION IF EXISTS vector")
