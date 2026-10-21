"""Users, beta allowlist, research runs, run citations and the LLM response cache.

Plain SQL so the migration stays frozen when `api.schema` changes later.
`tests/integration/test_migrations.py` checks that both describe the same schema.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENUMS = {
    "auth_provider": ("google", "github"),
    "run_status": ("running", "completed", "partial", "refused", "no_evidence", "failed"),
}

_TABLES = ("run_citations", "runs", "beta_allowlist", "users", "llm_cache")

_DDL = [
    """
    CREATE TABLE users (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        email TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        auth_provider auth_provider NOT NULL,
        provider_subject TEXT NOT NULL,
        language language NOT NULL DEFAULT 'tr',
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
        last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
        CONSTRAINT uq_users_provider_subject UNIQUE (auth_provider, provider_subject),
        CONSTRAINT ck_users_email_lower CHECK (email = lower(email)),
        CONSTRAINT ck_users_display_name_length CHECK (char_length(display_name) <= 200)
    )
    """,
    """
    CREATE TABLE beta_allowlist (
        email TEXT PRIMARY KEY,
        added_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
        note TEXT,
        CONSTRAINT ck_beta_allowlist_email_lower CHECK (email = lower(email))
    )
    """,
    """
    CREATE TABLE runs (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        question TEXT NOT NULL,
        language language NOT NULL,
        status run_status NOT NULL DEFAULT 'running',
        answer JSONB,
        usage JSONB NOT NULL DEFAULT '{}'::jsonb,
        error_code TEXT,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
        finished_at TIMESTAMP WITH TIME ZONE,
        CONSTRAINT ck_runs_question_length CHECK (char_length(question) BETWEEN 3 AND 1000)
    )
    """,
    "CREATE INDEX ix_runs_user_id_created_at ON runs (user_id, created_at)",
    "CREATE INDEX ix_runs_status ON runs (status)",
    """
    CREATE TABLE run_citations (
        run_id UUID NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
        cite_id TEXT NOT NULL,
        excerpt TEXT NOT NULL,
        source JSONB NOT NULL,
        CONSTRAINT pk_run_citations PRIMARY KEY (run_id, cite_id),
        CONSTRAINT ck_run_citations_cite_id CHECK (cite_id ~ '^[cf]:.+'),
        CONSTRAINT ck_run_citations_excerpt_length CHECK (char_length(excerpt) <= 1500)
    )
    """,
    """
    CREATE TABLE llm_cache (
        key CHAR(64) PRIMARY KEY,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        response JSONB NOT NULL,
        hits INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
        last_hit_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX ix_llm_cache_last_hit_at ON llm_cache (last_hit_at)",
]


def upgrade() -> None:
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
