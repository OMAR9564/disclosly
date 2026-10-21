"""Fixed-window rate limits (FR-022, research §R6).

Counters `rl:user:{id}:{minute}` and `rl:ip:{ip_hash}:{minute}` live for 120 s. Over the limit
the request gets `rate_limited` with `Retry-After` set to the seconds left in the window.

The client IP is Cloudflare's `CF-Connecting-IP`, but only when the socket peer is one of
`TRUSTED_PROXIES` (Caddy). From anywhere else the header is ignored and the peer address is
used, so a client that reaches the API directly cannot pick its own IP.
"""

import hashlib
import ipaddress
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from fastapi import Request

from api.errors import ApiError

WINDOW_SECONDS = 60
COUNTER_TTL_SECONDS = 120
CLIENT_IP_HEADER = "cf-connecting-ip"


class CounterRedis(Protocol):
    async def incr(self, name: str) -> Any: ...

    async def expire(self, name: str, time: int) -> Any: ...


def hash_ip(ip: str) -> str:
    return hashlib.sha256(ip.encode()).hexdigest()[:32]


def _is_trusted(peer: str, proxies: Sequence[str]) -> bool:
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(address in ipaddress.ip_network(net, strict=False) for net in proxies)


def client_ip(request: Request, trusted_proxies: Sequence[str]) -> str:
    peer = request.client.host if request.client else ""
    forwarded = request.headers.get(CLIENT_IP_HEADER, "").strip()
    if forwarded and _is_trusted(peer, trusted_proxies):
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            return peer
    return peer


class RateLimiter:
    def __init__(
        self,
        redis: CounterRedis,
        *,
        user_per_min: int,
        ip_per_min: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._redis = redis
        self._user_per_min = user_per_min
        self._ip_per_min = ip_per_min
        self._clock = clock

    async def _hit(self, key: str, limit: int) -> None:
        now = self._clock()
        minute = int(now // WINDOW_SECONDS)
        name = f"{key}:{minute}"
        count = int(await self._redis.incr(name))
        if count == 1:
            await self._redis.expire(name, COUNTER_TTL_SECONDS)
        if count > limit:
            retry_after = max(1, WINDOW_SECONDS - int(now % WINDOW_SECONDS))
            raise ApiError("rate_limited", headers={"Retry-After": str(retry_after)})

    async def check_ip(self, ip: str) -> None:
        await self._hit(f"rl:ip:{hash_ip(ip)}", self._ip_per_min)

    async def check_user(self, user_id: str) -> None:
        await self._hit(f"rl:user:{user_id}", self._user_per_min)
