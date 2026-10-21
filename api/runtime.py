"""Production wiring for the research runtime: DB engine, Redis, model router, tools."""

import os
from typing import Any, cast

import httpx
import redis.asyncio

from agent.loop import Budget
from agent.runs import RunService, StreamRedis
from api.config import Settings
from ingestion.db import make_engine
from ingestion.models import DocType
from llm.cache import CacheRedis, ResponseCache
from llm.config import load_providers
from llm.router import ModelRouter, RouterStore
from retrieval.types import DocumentMeta, SearchFilters, SearchHit
from tools.registry import ToolRegistry

ROUTER_TIMEOUT_SECONDS = 15.0


class LazySearch:
    """Loads the retrieval models on first use, not at API startup."""

    async def search(
        self, query: str, filters: SearchFilters | None = None, k: int = 6
    ) -> list[SearchHit]:
        from retrieval.search import search_documents

        return await search_documents(query, filters, k)

    async def list_documents(
        self,
        ticker: str,
        doc_types: list[DocType] | None = None,
        period_from: str | None = None,
        period_to: str | None = None,
    ) -> list[DocumentMeta]:
        from retrieval.search import list_documents

        return await list_documents(ticker, doc_types, period_from, period_to)


def budget_from(settings: Settings) -> Budget:
    return Budget(
        max_model_calls=settings.agent_max_model_calls,
        max_tool_calls=settings.agent_max_tool_calls,
        deadline_seconds=settings.agent_deadline_seconds,
    )


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
    service = RunService(
        engine,
        cast(StreamRedis, client),
        router,
        ToolRegistry(LazySearch(), engine),
        budget_from(settings),
    )
    return service, [engine, client, http]
