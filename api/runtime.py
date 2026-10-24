"""Production wiring for the research runtime: DB engine, Redis, model router, tools, graph."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import httpx
import redis.asyncio
from sqlalchemy import Engine

from agent.budget import ModeBudget
from agent.checkpoint import open_checkpointer
from agent.graph.build import build_graph
from agent.graph.context import GraphDeps
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
from tools.citations import CorpusCitations
from tools.python import SandboxClient
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
        quota=Quota(
            cast(QuotaRedis, client),
            settings.quota_daily_questions,
            deep_limit=settings.quota_daily_deep,
        ),
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
        agent_engine=settings.agent_engine,
        max_concurrent_runs=settings.agent_max_concurrent_runs,
        plan_ttl_hours=settings.agent_plan_ttl_hours,
    )
    return service, [engine, client, http, search]


def graph_deps(
    service: RunService, settings: Settings, sandbox: SandboxClient | None = None
) -> GraphDeps:
    tools = service.tools
    assert isinstance(tools, ToolRegistry)  # noqa: S101  (built by build_run_service)
    return GraphDeps(
        router=service.router,
        tools=tools,
        budgets={"simple": ModeBudget.simple(settings), "deep": ModeBudget.deep(settings)},
        emit=service.emit_event,
        attempts=service.attempt,
        plan_max_steps=settings.agent_plan_max_steps,
        plan_ttl_hours=settings.agent_plan_ttl_hours,
        verify_max_rounds=settings.agent_verify_max_rounds,
        quota=Quota(
            cast(QuotaRedis, service.redis),
            settings.quota_daily_questions,
            deep_limit=settings.quota_daily_deep,
        ),
        runs=service,
        sandbox=sandbox,
        citations=CorpusCitations(service.engine),
    )


@asynccontextmanager
async def graph_runtime(service: RunService, settings: Settings) -> AsyncIterator[None]:
    """Open the checkpointer and attach the compiled graph for the app's lifetime.

    The graph is built whatever `AGENT_ENGINE` says, so runs started by the graph engine can
    still be decided and resumed after a switch back to `single`.
    """
    sandbox = SandboxClient(settings.sandbox_url)
    async with open_checkpointer(settings.database_url.get_secret_value()) as saver:
        service.attach_graph(build_graph(graph_deps(service, settings, sandbox), saver))
        try:
            yield
        finally:
            service.attach_graph(None)
            await sandbox.aclose()
