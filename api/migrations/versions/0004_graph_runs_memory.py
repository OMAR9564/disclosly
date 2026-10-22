"""Graph runs (plans, decisions, deadlines, resumes), calculation citations and user memory.

specs/004-multi-agent-graph/data-model.md. Plain SQL so the migration stays frozen when
`api.schema` changes later; `tests/integration/test_migrations.py` checks that both describe
the same schema.

`run_status` gains three values. `ALTER TYPE ... ADD VALUE` would leave them unusable inside
this transaction (the new check constraint names one), so the type is recreated instead, the
same way the downgrade removes them.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_STATUSES = ("running", "completed", "partial", "refused", "no_evidence", "failed")
_NEW_STATUSES = (*_OLD_STATUSES, "awaiting_approval", "cancelled", "expired")

_ENUMS = {
    "run_mode": ("simple", "deep"),
    "run_engine": ("graph", "single"),
    "plan_decision": ("approve", "edit", "cancel", "simple"),
    "period_range": ("last_4q", "last_8q", "last_2fy", "last_3fy"),
}


def _labels(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _recreate_run_status(values: Sequence[str]) -> None:
    op.execute("ALTER TABLE runs ALTER COLUMN status DROP DEFAULT")
    op.execute("ALTER TYPE run_status RENAME TO run_status_old")
    op.execute(f"CREATE TYPE run_status AS ENUM ({_labels(values)})")
    op.execute(
        "ALTER TABLE runs ALTER COLUMN status TYPE run_status USING status::text::run_status"
    )
    op.execute("ALTER TABLE runs ALTER COLUMN status SET DEFAULT 'running'")
    op.execute("DROP TYPE run_status_old")


_UPGRADE = [
    # Existing rows were run by the Phase 3 loop; new rows default to the graph.
    "ALTER TABLE runs ADD COLUMN engine run_engine NOT NULL DEFAULT 'single'",
    "ALTER TABLE runs ALTER COLUMN engine SET DEFAULT 'graph'",
    "ALTER TABLE runs ADD COLUMN mode run_mode",
    "ALTER TABLE runs ADD COLUMN plan JSONB",
    "ALTER TABLE runs ADD COLUMN plan_expires_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE runs ADD COLUMN decided_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE runs ADD COLUMN decision plan_decision",
    "ALTER TABLE runs ADD COLUMN deadline_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE runs ADD COLUMN resume_count INTEGER NOT NULL DEFAULT 0",
    """
    ALTER TABLE runs ADD CONSTRAINT ck_runs_awaiting_has_expiry
        CHECK (status <> 'awaiting_approval' OR plan_expires_at IS NOT NULL)
    """,
    "CREATE INDEX ix_runs_status_plan_expires_at ON runs (status, plan_expires_at)",
    "ALTER TABLE run_citations DROP CONSTRAINT ck_run_citations_cite_id",
    "ALTER TABLE run_citations ADD CONSTRAINT ck_run_citations_cite_id"
    " CHECK (cite_id ~ '^[cfk]:.+')",
    """
    CREATE TABLE watchlist_items (
        user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        company_id BIGINT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
        added_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
        CONSTRAINT pk_watchlist_items PRIMARY KEY (user_id, company_id)
    )
    """,
    """
    CREATE TABLE user_preferences (
        user_id UUID PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
        answer_language language,
        default_period_range period_range,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
    )
    """,
]

_DOWNGRADE = [
    "DROP TABLE IF EXISTS user_preferences",
    "DROP TABLE IF EXISTS watchlist_items",
    # Calculation citations cannot exist under the old check.
    "DELETE FROM run_citations WHERE cite_id LIKE 'k:%'",
    "ALTER TABLE run_citations DROP CONSTRAINT ck_run_citations_cite_id",
    "ALTER TABLE run_citations ADD CONSTRAINT ck_run_citations_cite_id"
    " CHECK (cite_id ~ '^[cf]:.+')",
    "DROP INDEX IF EXISTS ix_runs_status_plan_expires_at",
    "ALTER TABLE runs DROP CONSTRAINT IF EXISTS ck_runs_awaiting_has_expiry",
    "ALTER TABLE runs DROP COLUMN resume_count",
    "ALTER TABLE runs DROP COLUMN deadline_at",
    "ALTER TABLE runs DROP COLUMN decision",
    "ALTER TABLE runs DROP COLUMN decided_at",
    "ALTER TABLE runs DROP COLUMN plan_expires_at",
    "ALTER TABLE runs DROP COLUMN plan",
    "ALTER TABLE runs DROP COLUMN mode",
    "ALTER TABLE runs DROP COLUMN engine",
    # Statuses the old type does not have end as failed runs.
    """
    UPDATE runs SET status = 'failed', error_code = COALESCE(error_code, 'interrupted'),
        finished_at = COALESCE(finished_at, now())
    WHERE status IN ('awaiting_approval', 'cancelled', 'expired')
    """,
]


def upgrade() -> None:
    _recreate_run_status(_NEW_STATUSES)
    for enum_name, values in _ENUMS.items():
        op.execute(f"CREATE TYPE {enum_name} AS ENUM ({_labels(values)})")
    for statement in _UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWNGRADE:
        op.execute(statement)
    for enum_name in _ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
    _recreate_run_status(_OLD_STATUSES)
