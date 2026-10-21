"""Shared helpers for agent/API integration tests: users, redis, scripted model."""

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio
from sqlalchemy import Engine, insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api import schema
from tests.agent_fakes import (
    FACT,
    FACTS_CALL,
    GOOD,
    FakeTools,
    ScriptedRouter,
    answer_response,
    tool_response,
)

__all__ = [
    "FACT",
    "FakeTools",
    "ScriptedRouter",
    "add_user",
    "good_script",
    "redis_client",
]


def add_user(
    engine: Engine, email: str = "user@example.test", *, allowlisted: bool = True
) -> uuid.UUID:
    with engine.begin() as conn:
        if allowlisted:
            conn.execute(
                pg_insert(schema.beta_allowlist).values(email=email).on_conflict_do_nothing()
            )
        user_id = conn.execute(
            insert(schema.users)
            .values(
                email=email,
                display_name="Test User",
                auth_provider="google",
                provider_subject=email,
            )
            .returning(schema.users.c.id)
        ).scalar_one()
    assert isinstance(user_id, uuid.UUID)
    return user_id


def good_script() -> ScriptedRouter:
    return ScriptedRouter([tool_response(*FACTS_CALL), answer_response(**GOOD)])


async def redis_client() -> AsyncIterator[Any]:
    client = redis.asyncio.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    yield client
    await client.aclose()
