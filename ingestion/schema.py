"""Corpus tables (data-model.md). Migration `0002_corpus` creates exactly these."""

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CHAR, JSONB, TSVECTOR

from ingestion.models import DocType, DocumentStatus, Language

EMBEDDING_DIM = 384


def _values(enum: type[DocType] | type[DocumentStatus] | type[Language]) -> list[str]:
    return [member.value for member in enum]


market_enum = Enum("bist", "us", name="market")
source_enum = Enum("kap", "sec", name="document_source")
doc_type_enum = Enum(*_values(DocType), name="doc_type")
language_enum = Enum(*_values(Language), name="language")
status_enum = Enum(*_values(DocumentStatus), name="document_status")
chunk_kind_enum = Enum("text", "table", name="chunk_kind")
run_trigger_enum = Enum("manual", "schedule", name="run_trigger")

# Postgres picks the text search config per row from the chunk language.
TSV_EXPRESSION = (
    "to_tsvector(CASE WHEN language = 'tr' THEN 'tr_unaccent'::regconfig "
    "ELSE 'en_unaccent'::regconfig END, text)"
)

metadata = MetaData()

companies = Table(
    "companies",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("ticker", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("market", market_enum, nullable=False),
    Column("kap_member_id", Text),
    Column("sec_cik", String(10)),
    CheckConstraint("ticker = upper(ticker)", name="ck_companies_ticker_upper"),
    CheckConstraint(
        "market <> 'bist' OR kap_member_id IS NOT NULL", name="ck_companies_bist_kap_member_id"
    ),
    CheckConstraint("market <> 'us' OR sec_cik ~ '^[0-9]{10}$'", name="ck_companies_us_sec_cik"),
)

documents = Table(
    "documents",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("company_id", BigInteger, ForeignKey("companies.id"), nullable=False),
    Column("source", source_enum, nullable=False),
    Column("doc_type", doc_type_enum, nullable=False),
    Column("period", Text, nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False),
    Column("source_url", Text, nullable=False),
    Column("content_hash", CHAR(64), nullable=False),
    Column("language", language_enum, nullable=False),
    Column("status", status_enum, nullable=False),
    Column("failure_reason", Text),
    Column("supersedes_id", BigInteger, ForeignKey("documents.id")),
    Column("attempts", Integer, nullable=False, server_default="0"),
    UniqueConstraint("source_url", "content_hash", name="uq_documents_source_url_content_hash"),
    CheckConstraint(
        "(status = 'failed') = (failure_reason IS NOT NULL)",
        name="ck_documents_failure_reason",
    ),
    Index("ix_documents_company_id", "company_id"),
)

sections = Table(
    "sections",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column(
        "document_id", BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    ),
    Column("ordinal", Integer, nullable=False),
    Column("label", Text, nullable=False),
    Column("title", Text),
    Column("page_start", Integer),
    Column("page_end", Integer),
    UniqueConstraint("document_id", "ordinal", name="uq_sections_document_id_ordinal"),
)

tables = Table(
    "tables",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column(
        "document_id", BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    ),
    Column("section_id", BigInteger, ForeignKey("sections.id", ondelete="CASCADE")),
    Column("page", Integer),
    Column("header", JSONB, nullable=False),
    Column("rows", JSONB, nullable=False),
    Index("ix_tables_document_id", "document_id"),
)

chunks = Table(
    "chunks",
    metadata,
    Column("id", CHAR(64), primary_key=True),
    Column(
        "document_id", BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    ),
    Column("section_id", BigInteger, ForeignKey("sections.id", ondelete="CASCADE")),
    Column("chunker_version", Text, nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("kind", chunk_kind_enum, nullable=False),
    Column("text", Text, nullable=False),
    Column("token_count", Integer, nullable=False),
    Column("page", Integer),
    Column("company_id", BigInteger, ForeignKey("companies.id"), nullable=False),
    Column("ticker", Text, nullable=False),
    Column("period", Text, nullable=False),
    Column("doc_type", doc_type_enum, nullable=False),
    Column("language", language_enum, nullable=False),
    Column("embedding", Vector(EMBEDDING_DIM)),
    Column("tsv", TSVECTOR, Computed(TSV_EXPRESSION, persisted=True)),
    UniqueConstraint(
        "document_id", "chunker_version", "ordinal", name="uq_chunks_document_version_ordinal"
    ),
    Index(
        "ix_chunks_embedding_hnsw",
        "embedding",
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    ),
    Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    Index("ix_chunks_filters", "ticker", "period", "doc_type", "language", "chunker_version"),
)

financial_facts = Table(
    "financial_facts",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("company_id", BigInteger, ForeignKey("companies.id"), nullable=False),
    Column("metric", Text, nullable=False),
    Column("period", Text, nullable=False),
    Column("value", Numeric, nullable=False),
    Column("unit", Text, nullable=False),
    Column("currency", Text),
    Column(
        "source_document_id",
        BigInteger,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("source_ref", Text, nullable=False),
    Column("derived", Boolean, nullable=False, server_default="false"),
    UniqueConstraint(
        "company_id",
        "metric",
        "period",
        "source_document_id",
        name="uq_financial_facts_company_metric_period_document",
    ),
)

ingestion_runs = Table(
    "ingestion_runs",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
    Column("trigger", run_trigger_enum, nullable=False),
    Column("fetched", Integer, nullable=False, server_default="0"),
    Column("skipped", Integer, nullable=False, server_default="0"),
    Column("failed", Integer, nullable=False, server_default="0"),
    Column("notes", JSONB, nullable=False, server_default="{}"),
)
