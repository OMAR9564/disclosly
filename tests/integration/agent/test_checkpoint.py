"""agent.checkpoint (T006): the saver's tables live only in schema `agent_graph`."""

import os
import uuid
from typing import TypedDict

import psycopg
import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from sqlalchemy import Engine

from agent.checkpoint import SCHEMA, main, open_checkpointer, setup_checkpointer

CHECKPOINT_TABLES = {
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
}


def _url() -> str:
    return os.environ.get(
        "DATABASE_URL", "postgresql://disclosly:CHANGE_ME@localhost:5432/disclosly"
    )


def _tables(schema: str) -> set[str]:
    with psycopg.connect(_url()) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = %s", (schema,)
        ).fetchall()
    return {str(row[0]) for row in rows}


async def test_setup_is_idempotent_and_stays_in_its_schema(engine: Engine) -> None:
    await setup_checkpointer(_url())
    await setup_checkpointer(_url())

    assert _tables(SCHEMA) >= CHECKPOINT_TABLES
    assert _tables("public").isdisjoint(CHECKPOINT_TABLES)


class _Counter(TypedDict):
    count: int


async def test_graph_state_round_trips_through_the_saver(engine: Engine) -> None:
    await setup_checkpointer(_url())
    builder = StateGraph(_Counter)
    builder.add_node("bump", lambda state: {"count": state["count"] + 1})
    builder.add_edge(START, "bump")
    builder.add_edge("bump", END)
    config: RunnableConfig = {"configurable": {"thread_id": str(uuid.uuid4())}}

    async with open_checkpointer(_url()) as saver:
        graph = builder.compile(checkpointer=saver)
        await graph.ainvoke({"count": 1}, config, durability="sync")
    async with open_checkpointer(_url()) as saver:
        snapshot = await builder.compile(checkpointer=saver).aget_state(config)

    assert snapshot.values == {"count": 2}


def test_main_runs_setup(engine: Engine) -> None:
    assert main(["setup"]) == 0
    assert _tables(SCHEMA) >= CHECKPOINT_TABLES


@pytest.mark.parametrize("argv", [[], ["teardown"], ["setup", "extra"]])
def test_main_rejects_other_commands(argv: list[str]) -> None:
    assert main(argv) == 2
