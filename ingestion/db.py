"""Database engine for ingestion and retrieval (SQLAlchemy Core, psycopg 3)."""

from sqlalchemy import Engine, create_engine, text


def sqlalchemy_url(database_url: str) -> str:
    """`postgresql://` URLs from the environment use the psycopg 3 driver."""
    for prefix in ("postgresql://", "postgres://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url[len(prefix) :]
    return database_url


def make_engine(database_url: str) -> Engine:
    return create_engine(sqlalchemy_url(database_url), pool_pre_ping=True)


def database_size_bytes(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text("SELECT pg_database_size(current_database())")).scalar_one())
