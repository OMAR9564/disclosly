"""Tests for api.auth.allowlist (T033)."""

from api.auth.allowlist import (
    CACHE_TTL_SECONDS,
    Allowlist,
    cache_key,
    email_hash,
    mask_email,
    normalize_email,
)
from tests.api_fakes import FakeRedis


def test_normalize_and_hash_ignore_case_and_spaces() -> None:
    assert normalize_email("  Ada@Example.COM ") == "ada@example.com"
    assert email_hash("Ada@Example.com") == email_hash("ada@example.com")


def test_mask_email() -> None:
    assert mask_email("Ada.Lovelace@example.com") == "a***@example.com"
    assert mask_email("not-an-email") == "***"


async def test_result_is_cached_for_60_seconds() -> None:
    redis = FakeRedis()
    calls: list[str] = []
    listed = {email_hash("ada@example.com")}

    def lookup(hashed: str) -> bool:
        calls.append(hashed)
        return hashed in listed

    allowlist = Allowlist(redis, lookup)
    assert await allowlist.allows("ada@example.com")
    assert await allowlist.allows("ADA@example.com")
    assert len(calls) == 1
    key = cache_key(email_hash("ada@example.com"))
    assert redis.values[key] == "1"
    assert redis.ttls[key] == CACHE_TTL_SECONDS
    assert not await allowlist.allows("eve@example.com")
    assert redis.values[cache_key(email_hash("eve@example.com"))] == "0"


async def test_removal_takes_effect_after_cache_ttl() -> None:
    redis = FakeRedis()
    listed = {email_hash("ada@example.com")}
    allowlist = Allowlist(redis, lambda hashed: hashed in listed)
    assert await allowlist.allows("ada@example.com")

    listed.clear()
    assert await allowlist.allows("ada@example.com")  # still cached
    redis.expire_all()  # 60 s later
    assert not await allowlist.allows("ada@example.com")


async def test_bytes_from_redis_are_read() -> None:
    redis = FakeRedis()
    await redis.set(cache_key(email_hash("ada@example.com")), b"1")  # type: ignore[arg-type]
    assert await Allowlist(redis, lambda hashed: False).allows("ada@example.com")
