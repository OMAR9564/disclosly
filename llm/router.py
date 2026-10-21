"""Model router: walk the providers of a task class in order (contracts/model-router.md).

For each provider the router checks, in order: the response cache (a hit is free), the
cooldown key `llm:cooldown:{provider}`, and today's usage `llm:usage:{provider}:{yyyymmdd}`.
Failures are classified per the contract's table and put the provider in cooldown. Without a
`store` (unit tests, evals) there are no cooldowns or quotas.
"""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from llm.cache import ResponseCache
from llm.cache_key import cache_key
from llm.client import (
    InvalidResponse,
    ProviderConfigRejected,
    ProviderError,
    RateLimited,
    Unavailable,
    chat_completion,
)
from llm.config import Provider, RouterConfig
from llm.telemetry import chat_span, record_response
from llm.types import ChatRequest, ChatResponse, ProvidersUnavailable, TaskClass

logger = logging.getLogger(__name__)

RATE_LIMIT_COOLDOWN = 60
UNAVAILABLE_COOLDOWN = 60
INVALID_COOLDOWN = 30
CONFIG_ERROR_COOLDOWN = 60 * 60
USAGE_TTL_SECONDS = 48 * 60 * 60
QUOTA_SKIP_RATIO = 0.9


class Router(Protocol):
    async def chat(self, request: ChatRequest, *, task_class: TaskClass) -> ChatResponse: ...


class RouterStore(Protocol):
    """The subset of `redis.asyncio.Redis` the router uses."""

    async def get(self, name: str) -> Any: ...

    async def set(self, name: str, value: str, ex: int | None = None) -> Any: ...

    async def incr(self, name: str) -> Any: ...

    async def expire(self, name: str, time: int) -> Any: ...


def cooldown_key(provider: str) -> str:
    return f"llm:cooldown:{provider}"


def usage_key(provider: str, now: datetime) -> str:
    return f"llm:usage:{provider}:{now.astimezone(UTC):%Y%m%d}"


def cooldown_seconds(exc: ProviderError) -> int:
    """Cooldown for a failure, per the contract's failure classification table."""
    if isinstance(exc, RateLimited):
        return round(exc.retry_after) if exc.retry_after is not None else RATE_LIMIT_COOLDOWN
    if isinstance(exc, InvalidResponse):
        return INVALID_COOLDOWN
    if isinstance(exc, ProviderConfigRejected):
        return CONFIG_ERROR_COOLDOWN
    if isinstance(exc, Unavailable):
        return UNAVAILABLE_COOLDOWN
    return UNAVAILABLE_COOLDOWN


def cacheable(request: ChatRequest, response: ChatResponse) -> bool:
    """Only schema-valid successes are cached: structured output must be a JSON object."""
    if request.response_schema is None:
        return True
    try:
        return isinstance(json.loads(response.content or ""), dict)
    except ValueError:
        return False


class ModelRouter:
    def __init__(
        self,
        config: RouterConfig,
        http: httpx.AsyncClient,
        *,
        store: RouterStore | None = None,
        cache: ResponseCache | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._config = config
        self._http = http
        self._store = store
        self._cache = cache
        self._now = now

    async def _available(self, provider: Provider) -> bool:
        if self._store is None:
            return True
        if await self._store.get(cooldown_key(provider.name)) is not None:
            logger.info("provider cooling down", extra={"provider": provider.name})
            return False
        used = int(await self._store.get(usage_key(provider.name, self._now())) or 0)
        if used >= QUOTA_SKIP_RATIO * provider.daily_requests:
            logger.info("provider near daily quota", extra={"provider": provider.name})
            return False
        return True

    async def _count(self, provider: Provider) -> None:
        if self._store is None:
            return
        key = usage_key(provider.name, self._now())
        await self._store.incr(key)
        await self._store.expire(key, USAGE_TTL_SECONDS)

    async def _cool_down(self, provider: Provider, exc: ProviderError) -> None:
        seconds = cooldown_seconds(exc)
        if isinstance(exc, ProviderConfigRejected):
            logger.error(
                "provider config error",
                extra={"provider": provider.name, "status": exc.status},
            )
        else:
            logger.warning(
                "provider failed",
                extra={"provider": provider.name, "error_type": type(exc).__name__},
            )
        if self._store is not None and seconds > 0:
            await self._store.set(cooldown_key(provider.name), "1", ex=seconds)

    async def chat(self, request: ChatRequest, *, task_class: TaskClass) -> ChatResponse:
        passed: list[str] = []
        for provider in self._config.providers_for(task_class):
            key = cache_key(request, provider.model)
            if self._cache is not None:
                cached = await self._cache.get(key)
                if cached is not None:
                    hit = cached.model_copy(
                        update={"cache_hit": True, "latency_ms": 0.0, "fallback_from": passed}
                    )
                    with chat_span(
                        provider=provider.name, model=provider.model, task_class=task_class
                    ) as span:
                        record_response(span, hit, provider.price_per_mtok)
                    return hit
            if not await self._available(provider):
                passed.append(provider.name)
                continue
            with chat_span(
                provider=provider.name, model=provider.model, task_class=task_class
            ) as span:
                await self._count(provider)
                try:
                    response = await chat_completion(self._http, provider, request)
                except ProviderError as exc:
                    span.set_attribute("disclosly.error_type", type(exc).__name__)
                    await self._cool_down(provider, exc)
                    passed.append(provider.name)
                    continue
                response = response.model_copy(update={"fallback_from": passed})
                record_response(span, response, provider.price_per_mtok)
            if self._cache is not None and cacheable(request, response):
                await self._cache.put(key, response)
            return response
        raise ProvidersUnavailable(task_class, passed)
