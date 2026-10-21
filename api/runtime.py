"""Production wiring for the research runtime: DB engine, Redis, model router, tools."""

import os
from typing import Any, cast

import httpx
import redis.asyncio
from sqlalchemy import Engine

from agent.loop import Budget
from agent.runs import RunService, StreamRedis
from api.auth.allowlist import Allowlist, AllowRedis, db_lookup
from api.config import Settings
from api.guards import Guards
from api.guards.quota import Quota, QuotaRedis
from api.guards.ratelimit import CounterRedis, RateLimiter
from api.search_client import RemoteSearch
from ingestion.db import make_engine
from llm.cache import CacheRedis, ResponseCache
from llm.config import load_providers
from llm.router import ModelRouter, RouterStore
from tools.registry import ToolRegistry

ROUTER_TIMEOUT_SECONDS = 15.0
GUARD_HTTP_TIMEOUT_SECONDS = 10.0


def budget_from(settings: Settings) -> Budget:
    return Budget(
        max_model_calls=settings.agent_max_model_calls,
        max_tool_calls=settings.agent_max_tool_calls,
        deadline_seconds=settings.agent_deadline_seconds,
    )


def build_guards(settings: Settings, engine: Engine) -> tuple[Guards, list[Any]]:
    """Allowlist, quota and rate limits on their own Redis client and HTTP client."""
    client = redis.asyncio.from_url(settings.redis_url)
    http = httpx.AsyncClient(timeout=GUARD_HTTP_TIMEOUT_SECONDS)
    guards = Guards(
        engine=engine,
        redis=client,
        http=http,
        allowlist=Allowlist(cast(AllowRedis, client), db_lookup(engine)),
        quota=Quota(cast(QuotaRedis, client), settings.quota_daily_questions),
        ratelimit=RateLimiter(
            cast(CounterRedis, client),
            user_per_min=settings.rate_limit_user_per_min,
            ip_per_min=settings.rate_limit_ip_per_min,
        ),
    )
    return guards, [client, http]


def build_run_service(settings: Settings) -> tuple[RunService, list[Any]]:
    """The service plus the resources to close on shutdown."""
    engine = make_engine(settings.database_url.get_secret_value())
    client = redis.asyncio.from_url(settings.redis_url)
    http = httpx.AsyncClient(timeout=ROUTER_TIMEOUT_SECONDS)
    router = ModelRouter(
        load_providers(settings.llm_providers_file, os.environ),
        http,
        store=cast(RouterStore, client),
        cache=ResponseCache(cast(CacheRedis, client), engine),
    )
    search = RemoteSearch(settings.redis_url)
    service = RunService(
        engine,
        cast(StreamRedis, client),
        router,
        ToolRegistry(search, engine),
        budget_from(settings),
    )
    return service, [engine, client, http, search]
