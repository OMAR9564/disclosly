"""Tests for api.guards.ratelimit (T035)."""

import pytest
from starlette.requests import Request

from api.errors import ApiError
from api.guards.ratelimit import COUNTER_TTL_SECONDS, RateLimiter, client_ip, hash_ip
from tests.api_fakes import FakeRedis

CADDY = ["172.30.0.0/24"]


def _request(peer: str, headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw, "client": (peer, 12345)})


def test_cf_header_trusted_from_caddy() -> None:
    request = _request("172.30.0.5", {"CF-Connecting-IP": "203.0.113.7"})
    assert client_ip(request, CADDY) == "203.0.113.7"


def test_spoofed_header_from_outside_proxy_is_ignored() -> None:
    request = _request("198.51.100.9", {"CF-Connecting-IP": "203.0.113.7"})
    assert client_ip(request, CADDY) == "198.51.100.9"


def test_header_ignored_when_no_proxy_is_trusted() -> None:
    request = _request("172.30.0.5", {"CF-Connecting-IP": "203.0.113.7"})
    assert client_ip(request, []) == "172.30.0.5"


def test_garbage_header_falls_back_to_peer() -> None:
    request = _request("172.30.0.5", {"CF-Connecting-IP": "not-an-ip"})
    assert client_ip(request, CADDY) == "172.30.0.5"


async def test_user_limit_and_retry_after() -> None:
    redis = FakeRedis()
    limiter = RateLimiter(redis, user_per_min=2, ip_per_min=10, clock=lambda: 6000 + 15.0)
    await limiter.check_user("u1")
    await limiter.check_user("u1")
    with pytest.raises(ApiError) as excinfo:
        await limiter.check_user("u1")
    assert excinfo.value.code == "rate_limited"
    assert excinfo.value.headers == {"Retry-After": "45"}
    assert redis.ttls["rl:user:u1:100"] == COUNTER_TTL_SECONDS
    await limiter.check_user("u2")  # other users are not affected


async def test_ip_limit_uses_hashed_ip_and_new_window_resets() -> None:
    redis = FakeRedis()
    now = [120.0]
    limiter = RateLimiter(redis, user_per_min=10, ip_per_min=1, clock=lambda: now[0])
    await limiter.check_ip("203.0.113.7")
    assert f"rl:ip:{hash_ip('203.0.113.7')}:2" in redis.values
    assert "203.0.113.7" not in "".join(redis.values)
    with pytest.raises(ApiError):
        await limiter.check_ip("203.0.113.7")
    now[0] = 180.0
    await limiter.check_ip("203.0.113.7")
