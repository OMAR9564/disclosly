"""Deep quota swap (T017) against a real Redis: concurrency and the submission day."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest

from api.errors import ApiError
from api.guards.quota import QUOTA_TTL_SECONDS, Quota, deep_quota_key, quota_key
from tests.integration.agent.helpers import redis_client


@pytest.fixture
async def redis() -> AsyncIterator[Any]:
    async for client in redis_client():
        yield client


async def test_concurrent_swaps_never_exceed_the_deep_limit(redis: Any) -> None:
    user = uuid.uuid4()
    quota = Quota(redis, 20, deep_limit=2)
    now = datetime.now(UTC)
    for _ in range(5):
        await quota.take(user)

    results = await asyncio.gather(
        *(quota.swap_to_deep(user, now) for _ in range(5)), return_exceptions=True
    )

    accepted = [r for r in results if not isinstance(r, BaseException)]
    refused = [r for r in results if isinstance(r, ApiError)]
    assert len(accepted) == 2
    assert {r.code for r in refused} == {"quota_exhausted"}
    assert int(await redis.get(deep_quota_key(user, now))) == 2
    # Only the two accepted swaps gave their simple unit back.
    assert int(await redis.get(quota_key(user, now))) == 3
    assert 0 < await redis.ttl(deep_quota_key(user, now)) <= QUOTA_TTL_SECONDS
    await redis.delete(deep_quota_key(user, now), quota_key(user, now))


async def test_swap_after_midnight_refunds_the_submission_day(redis: Any) -> None:
    user = uuid.uuid4()
    submitted = datetime(2026, 10, 21, 20, 50, tzinfo=UTC)  # 23:50 in Istanbul
    approved = datetime(2026, 10, 21, 21, 10, tzinfo=UTC)  # 00:10 the next day
    await Quota(redis, 20, now=lambda: submitted).take(user)

    await Quota(redis, 20, now=lambda: approved, deep_limit=1).swap_to_deep(user, submitted)

    assert int(await redis.get(quota_key(user, submitted))) == 0
    assert await redis.get(quota_key(user, approved)) is None
    assert int(await redis.get(deep_quota_key(user, approved))) == 1
    await redis.delete(
        quota_key(user, submitted), deep_quota_key(user, approved), quota_key(user, approved)
    )
