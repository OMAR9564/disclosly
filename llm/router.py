"""Model router: walk the providers of a task class in order (contracts/model-router.md).

This is the basic walk. Cooldowns, quota-aware skipping and the response cache are added
for US2 (tasks T025-T027).
"""

import logging
from typing import Protocol

import httpx

from llm.client import ProviderError, chat_completion
from llm.config import RouterConfig
from llm.types import ChatRequest, ChatResponse, ProvidersUnavailable, TaskClass

logger = logging.getLogger(__name__)


class Router(Protocol):
    async def chat(self, request: ChatRequest, *, task_class: TaskClass) -> ChatResponse: ...


class ModelRouter:
    def __init__(self, config: RouterConfig, http: httpx.AsyncClient) -> None:
        self._config = config
        self._http = http

    async def chat(self, request: ChatRequest, *, task_class: TaskClass) -> ChatResponse:
        failed: list[str] = []
        for provider in self._config.providers_for(task_class):
            try:
                response = await chat_completion(self._http, provider, request)
            except ProviderError as exc:
                logger.warning(
                    "provider failed",
                    extra={"provider": provider.name, "error_type": type(exc).__name__},
                )
                failed.append(provider.name)
                continue
            return response.model_copy(update={"fallback_from": failed})
        raise ProvidersUnavailable(task_class, failed)
