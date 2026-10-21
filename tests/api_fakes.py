"""In-memory stand-ins for the API guards: a small Redis fake and a `Guards` factory."""

from typing import Any, cast

import httpx
from sqlalchemy import Engine

from api.auth.allowlist import Allowlist, email_hash
from api.guards import Guards
from api.guards.quota import _REFUND, _TAKE, Quota
from api.guards.ratelimit import RateLimiter


class FakeRedis:
    """get/set/incr/expire/getdel/delete plus the two quota scripts. TTLs are recorded, and
    `expire_all()` drops every key that has one."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, name: str) -> Any:
        return self.values.get(name)

    async def set(self, name: str, value: str, ex: int | None = None) -> Any:
        self.values[name] = value
        if ex is not None:
            self.ttls[name] = ex
        return True

    async def incr(self, name: str) -> Any:
        self.values[name] = str(int(self.values.get(name, "0")) + 1)
        return int(self.values[name])

    async def expire(self, name: str, time: int) -> Any:
        self.ttls[name] = time
        return True

    async def getdel(self, name: str) -> Any:
        self.ttls.pop(name, None)
        return self.values.pop(name, None)

    async def delete(self, *names: str) -> Any:
        return sum(self.values.pop(name, None) is not None for name in names)

    async def eval(self, script: str, numkeys: int, *args: Any) -> Any:
        key = str(args[0])
        used = int(self.values.get(key, "0"))
        if script == _TAKE:
            if used >= int(args[1]):
                return -1
            self.values[key] = str(used + 1)
            self.ttls.setdefault(key, int(args[2]))
            return used + 1
        if script == _REFUND:
            self.values[key] = str(max(used - 1, 0))
            return int(self.values[key])
        raise NotImplementedError(script)

    def expire_all(self) -> None:
        for name in list(self.ttls):
            self.values.pop(name, None)
        self.ttls.clear()


def fake_guards(
    allowed: set[str] | None = None,
    *,
    redis: FakeRedis | None = None,
    engine: Engine | None = None,
    http: httpx.AsyncClient | None = None,
    quota_limit: int = 20,
    user_per_min: int = 30,
    ip_per_min: int = 60,
) -> Guards:
    """Guards whose allowlist holds `allowed` (default: user@example.test)."""
    store = redis or FakeRedis()
    hashes = {email_hash(email) for email in (allowed or {"user@example.test"})}
    return Guards(
        engine=cast(Engine, engine),
        redis=store,
        http=http
        or httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
        allowlist=Allowlist(store, lambda hashed: hashed in hashes),
        quota=Quota(store, quota_limit),
        ratelimit=RateLimiter(store, user_per_min=user_per_min, ip_per_min=ip_per_min),
    )
