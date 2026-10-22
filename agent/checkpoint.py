"""LangGraph checkpoints in Postgres schema `agent_graph` (research §R1).

Alembic owns `public`; the saver's own tables live in `agent_graph`, reached through the
connection option `search_path=agent_graph`. The migrate service runs
`python -m agent.checkpoint setup` after `alembic upgrade head`.
"""

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from agent.graph.state import STATE_TYPES

SCHEMA = "agent_graph"
POOL_MAX_SIZE = 4
CONNECTION_OPTIONS = f"-c search_path={SCHEMA}"


def graph_serde() -> JsonPlusSerializer:
    """The checkpoint serializer, rebuilding only the graph state's own classes."""
    return JsonPlusSerializer(allowed_msgpack_modules=STATE_TYPES)


def _conninfo(database_url: str) -> str:
    """A libpq DSN: SQLAlchemy-style `postgresql+psycopg://` URLs lose their driver suffix."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _connection_kwargs() -> dict[str, Any]:
    # What the saver needs from every connection (see AsyncPostgresSaver.from_conn_string).
    return {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
        "options": CONNECTION_OPTIONS,
    }


@asynccontextmanager
async def open_checkpointer(database_url: str) -> AsyncIterator[AsyncPostgresSaver]:
    """A saver on its own small pool; the pool is closed on exit."""
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        _conninfo(database_url),
        max_size=POOL_MAX_SIZE,
        kwargs=_connection_kwargs(),
        open=False,
    )
    await pool.open()
    try:
        yield AsyncPostgresSaver(pool, serde=graph_serde())
    finally:
        await pool.close()


async def setup_checkpointer(database_url: str) -> None:
    """Create schema `agent_graph` if missing and the saver's tables in it (idempotent)."""
    async with await psycopg.AsyncConnection.connect(
        _conninfo(database_url), autocommit=True
    ) as conn:
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    async with open_checkpointer(database_url) as saver:
        await saver.setup()


def main(argv: list[str]) -> int:
    if argv != ["setup"]:
        print("usage: python -m agent.checkpoint setup", file=sys.stderr)
        return 2
    from api.config import load_settings

    settings = load_settings()
    asyncio.run(setup_checkpointer(settings.database_url.get_secret_value()))
    return 0


if __name__ == "__main__":  # pragma: no cover - run by the migrate service
    raise SystemExit(main(sys.argv[1:]))
