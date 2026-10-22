"""API tables (specs/003-single-agent-mvp/data-model.md, specs/004-multi-agent-graph/data-model.md).

Migrations `0003_users_runs_cache` and `0004_graph_runs_memory` create exactly these;
`tests/integration/test_migrations.py` checks that both describe the same schema.
"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CHAR, JSONB, UUID

from ingestion import schema as corpus_schema

RUN_STATUSES = (
    "running",
    "completed",
    "partial",
    "refused",
    "no_evidence",
    "failed",
    "awaiting_approval",
    "cancelled",
    "expired",
)
RUN_MODES = ("simple", "deep")
RUN_ENGINES = ("graph", "single")
PLAN_DECISIONS = ("approve", "edit", "cancel", "simple")
PERIOD_RANGES = ("last_4q", "last_8q", "last_2fy", "last_3fy")

metadata = MetaData()

# `language` is created by migration 0002 for the corpus tables; the API reuses it.
language_enum = Enum("tr", "en", name="language", create_type=False)
auth_provider_enum = Enum("google", "github", name="auth_provider")
run_status_enum = Enum(*RUN_STATUSES, name="run_status")
run_mode_enum = Enum(*RUN_MODES, name="run_mode")
run_engine_enum = Enum(*RUN_ENGINES, name="run_engine")
plan_decision_enum = Enum(*PLAN_DECISIONS, name="plan_decision")
period_range_enum = Enum(*PERIOD_RANGES, name="period_range")

_UUID_DEFAULT = text("gen_random_uuid()")

users = Table(
    "users",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=_UUID_DEFAULT),
    Column("email", Text, nullable=False, unique=True),
    Column("display_name", Text, nullable=False),
    Column("auth_provider", auth_provider_enum, nullable=False),
    Column("provider_subject", Text, nullable=False),
    Column("language", language_enum, nullable=False, server_default="tr"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_seen_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("auth_provider", "provider_subject", name="uq_users_provider_subject"),
    CheckConstraint("email = lower(email)", name="ck_users_email_lower"),
    CheckConstraint("char_length(display_name) <= 200", name="ck_users_display_name_length"),
)

beta_allowlist = Table(
    "beta_allowlist",
    metadata,
    Column("email", Text, primary_key=True),
    Column("added_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("note", Text),
    CheckConstraint("email = lower(email)", name="ck_beta_allowlist_email_lower"),
)

runs = Table(
    "runs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=_UUID_DEFAULT),
    Column(
        "user_id", UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    ),
    Column("question", Text, nullable=False),
    Column("language", language_enum, nullable=False),
    Column("status", run_status_enum, nullable=False, server_default="running"),
    Column("answer", JSONB),
    Column("usage", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("error_code", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
    Column("engine", run_engine_enum, nullable=False, server_default="graph"),
    Column("mode", run_mode_enum),
    Column("plan", JSONB),
    Column("plan_expires_at", DateTime(timezone=True)),
    Column("decided_at", DateTime(timezone=True)),
    Column("decision", plan_decision_enum),
    Column("deadline_at", DateTime(timezone=True)),
    Column("resume_count", Integer, nullable=False, server_default="0"),
    CheckConstraint("char_length(question) BETWEEN 3 AND 1000", name="ck_runs_question_length"),
    CheckConstraint(
        "status <> 'awaiting_approval' OR plan_expires_at IS NOT NULL",
        name="ck_runs_awaiting_has_expiry",
    ),
    Index("ix_runs_user_id_created_at", "user_id", "created_at"),
    Index("ix_runs_status", "status"),
    Index("ix_runs_status_plan_expires_at", "status", "plan_expires_at"),
)

run_citations = Table(
    "run_citations",
    metadata,
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
    Column("cite_id", Text, nullable=False),
    Column("excerpt", Text, nullable=False),
    Column("source", JSONB, nullable=False),
    PrimaryKeyConstraint("run_id", "cite_id", name="pk_run_citations"),
    CheckConstraint("cite_id ~ '^[cfk]:.+'", name="ck_run_citations_cite_id"),
    CheckConstraint("char_length(excerpt) <= 1500", name="ck_run_citations_excerpt_length"),
)

llm_cache = Table(
    "llm_cache",
    metadata,
    Column("key", CHAR(64), primary_key=True),
    Column("provider", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("response", JSONB, nullable=False),
    Column("hits", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_hit_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index("ix_llm_cache_last_hit_at", "last_hit_at"),
)

watchlist_items = Table(
    "watchlist_items",
    metadata,
    Column(
        "user_id", UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    ),
    # `companies` is a corpus table (ingestion.schema, its own MetaData).
    Column(
        "company_id",
        BigInteger,
        ForeignKey(corpus_schema.companies.c.id, ondelete="CASCADE"),
        nullable=False,
    ),
    Column("added_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("user_id", "company_id", name="pk_watchlist_items"),
)

user_preferences = Table(
    "user_preferences",
    metadata,
    Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("answer_language", language_enum),
    Column("default_period_range", period_range_enum),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

API_TABLES = frozenset(
    {
        "users",
        "beta_allowlist",
        "runs",
        "run_citations",
        "llm_cache",
        "watchlist_items",
        "user_preferences",
    }
)
