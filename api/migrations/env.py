"""Alembic environment: database URL and logging come from api.config.Settings."""

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import URL, make_url

from api.config import get_settings
from api.logging import configure_logging

# No ORM models yet; autogenerate gets a target in a later phase.
target_metadata = None


def _database_url() -> URL:
    # DATABASE_URL is a plain psycopg DSN; SQLAlchemy needs the psycopg 3 dialect named.
    url = make_url(get_settings().database_url.get_secret_value())
    return url.set(drivername="postgresql+psycopg")


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url().render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


configure_logging(get_settings().log_level)

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
