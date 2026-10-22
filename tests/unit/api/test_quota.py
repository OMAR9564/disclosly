"""api.guards.quota deep quota (T017) with the in-memory Redis fake."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from api.errors import ApiError
from api.guards.quota import QUOTA_TTL_SECONDS, Quota, deep_quota_key, quota_key
from tests.api_fakes import FakeRedis

NOON = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)  # 12:00 in Istanbul


def _quota(redis: FakeRedis, now: datetime = NOON, deep_limit: int = 2) -> Quota:
    return Quota(redis, 20, now=lambda: now, deep_limit=deep_limit)


def test_deep_key_format() -> None:
    user = uuid.UUID(int=1)
    assert deep_quota_key(user, NOON) == f"quota:deep:{user}:20261022"


async def test_swap_takes_deep_and_refunds_simple() -> None:
    redis, user = FakeRedis(), uuid.uuid4()
    quota = _quota(redis)
    await quota.take(user)

    deep = await quota.swap_to_deep(user, NOON)

    assert (deep.limit, deep.used, deep.remaining) == (2, 1, 1)
    assert (await quota.status(user)).used == 0
    assert redis.ttls[deep_quota_key(user, NOON)] == QUOTA_TTL_SECONDS
    statuses = await quota.statuses(user)
    assert (statuses.simple.used, statuses.deep.used) == (0, 1)
    assert await quota.deep_remaining(str(user)) == 1


async def test_swap_without_deep_units_changes_nothing() -> None:
    redis, user = FakeRedis(), uuid.uuid4()
    quota = _quota(redis, deep_limit=1)
    await quota.take(user)
    await quota.take(user)
    await quota.swap_to_deep(user, NOON)

    with pytest.raises(ApiError) as excinfo:
        await quota.swap_to_deep(user, NOON, "en")

    assert excinfo.value.code == "quota_exhausted"
    assert excinfo.value.params == {"resets_at": "00:00 (TRT)"}
    assert (await quota.status(user)).used == 1
    assert (await quota.deep_status(user)).used == 1


async def test_simple_refund_never_goes_below_zero() -> None:
    redis, user = FakeRedis(), uuid.uuid4()
    quota = _quota(redis)
    await quota.swap_to_deep(user, NOON)
    assert (await quota.status(user)).used == 0
    assert (await quota.deep_status(user)).used == 1


async def test_refund_after_midnight_targets_the_submission_day() -> None:
    redis, user = FakeRedis(), uuid.uuid4()
    submitted = NOON
    await _quota(redis, submitted).take(user)
    next_day = NOON + timedelta(days=1)

    await _quota(redis, next_day).swap_to_deep(user, submitted)

    assert redis.values[quota_key(user, submitted)] == "0"
    assert redis.values[deep_quota_key(user, next_day)] == "1"
    assert deep_quota_key(user, submitted) not in redis.values
