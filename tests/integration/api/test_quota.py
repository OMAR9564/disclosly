"""Integration tests for api.guards.quota (T034) against a real Redis."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest

from api.errors import ApiError
from api.guards.quota import (
    QUOTA_TTL_SECONDS,
    Quota,
    quota_day,
    quota_key,
    resets_at,
)
from tests.integration.agent.helpers import redis_client


@pytest.fixture
async def redis() -> AsyncIterator[Any]:
    async for client in redis_client():
        yield client


async def test_ten_concurrent_requests_with_limit_two(redis: Any) -> None:
    user = uuid.uuid4()
    quota = Quota(redis, 2)

    results = await asyncio.gather(*(quota.take(user) for _ in range(10)), return_exceptions=True)

    accepted = [r for r in results if not isinstance(r, BaseException)]
    refused = [r for r in results if isinstance(r, ApiError)]
    assert len(accepted) == 2
    assert len(refused) == 8
    assert {r.code for r in refused} == {"quota_exhausted"}
    key = quota_key(user, datetime.now(UTC))
    assert int(await redis.get(key)) == 2
    assert 0 < await redis.ttl(key) <= QUOTA_TTL_SECONDS
    await redis.delete(key)


async def test_refund_gives_a_question_back_and_never_goes_negative(redis: Any) -> None:
    user = uuid.uuid4()
    quota = Quota(redis, 1)
    status = await quota.take(user)
    assert (status.used, status.remaining) == (1, 0)
    now = datetime.now(UTC)
    await quota.refund(user, now)
    await quota.refund(user, now)
    assert (await quota.status(user)).used == 0
    await quota.take(user)
    await redis.delete(quota_key(user, now))


def test_day_is_istanbul_time() -> None:
    late_utc = datetime(2026, 10, 21, 21, 30, tzinfo=UTC)  # 00:30 on the 22nd in Istanbul
    assert quota_day(late_utc) == "20261022"
    assert resets_at(late_utc) == datetime(2026, 10, 22, 21, 0, tzinfo=UTC)
    assert resets_at(datetime(2026, 10, 21, 12, 0, tzinfo=UTC)) == datetime(
        2026, 10, 21, 21, 0, tzinfo=UTC
    )


async def test_exhausted_message_params(redis: Any) -> None:
    user = uuid.uuid4()
    quota = Quota(redis, 1, now=lambda: datetime(2026, 10, 21, 12, 0, tzinfo=UTC))
    await quota.take(user)
    with pytest.raises(ApiError) as excinfo:
        await quota.take(user, "tr")
    assert excinfo.value.params == {"resets_at": "00:00 (TSİ)"}
    await redis.delete(quota_key(user, datetime(2026, 10, 21, 12, 0, tzinfo=UTC)))
