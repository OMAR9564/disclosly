"""Request guards: Turnstile, quota, rate limits.

`Guards` bundles what the auth and research routes need; `create_app` builds it at startup
(or a test injects one) and stores it on `app.state.guards`.
"""

from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Request
from sqlalchemy import Engine

from api.auth.allowlist import Allowlist
from api.guards.quota import Quota
from api.guards.ratelimit import RateLimiter, client_ip


@dataclass(frozen=True)
class Guards:
    engine: Engine
    redis: Any
    http: httpx.AsyncClient
    allowlist: Allowlist
    quota: Quota
    ratelimit: RateLimiter


def get_guards(request: Request) -> Guards:
    guards = request.app.state.guards
    assert isinstance(guards, Guards)  # noqa: S101  (set at startup)
    return guards


async def ip_rate_limit(request: Request) -> None:
    """Router dependency: the per-IP limit for every /v1 request."""
    settings = request.app.state.settings
    await get_guards(request).ratelimit.check_ip(client_ip(request, settings.trusted_proxies))
