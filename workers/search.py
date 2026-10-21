"""Search worker (arq queue `arq:search`): answers the API's document searches.

The API image has no ML models, so `search_documents` and `list_documents` run here, next to
the embedding and rerank models (loaded once at startup). A separate queue and container keep
searches from waiting behind a long ingestion job on the main worker.

Results cross Redis as JSON-ready dicts; the API validates them back into its types.
"""

import os
from typing import Any, ClassVar

from arq.connections import RedisSettings
from arq.worker import func

from api.search_client import SEARCH_QUEUE
from ingestion.models import DocType
from retrieval.search import DEFAULT_K, SearchService, default_service
from retrieval.types import SearchFilters

SEARCH_JOB_TIMEOUT_SECONDS = 30

Ctx = dict[str, Any]


async def startup(ctx: Ctx) -> None:
    # Loading the models here makes the first search as fast as the rest.
    ctx["search"] = default_service()


def _service(ctx: Ctx) -> SearchService:
    service: SearchService = ctx["search"]
    return service


async def search_job(
    ctx: Ctx, query: str, filters: dict[str, Any] | None = None, k: int = DEFAULT_K
) -> list[dict[str, Any]]:
    parsed = SearchFilters.model_validate(filters) if filters else None
    hits = await _service(ctx).search(query, parsed, k)
    return [hit.model_dump(mode="json") for hit in hits]


async def list_documents_job(
    ctx: Ctx,
    ticker: str,
    doc_types: list[str] | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
) -> list[dict[str, Any]]:
    types = [DocType(value) for value in doc_types] if doc_types is not None else None
    documents = await _service(ctx).list_documents(ticker, types, period_from, period_to)
    return [document.model_dump(mode="json") for document in documents]


class SearchWorkerSettings:
    functions: ClassVar = [
        func(search_job, name="search"),
        func(list_documents_job, name="list_documents"),
    ]
    queue_name = SEARCH_QUEUE
    on_startup = startup
    # Searches are short and mostly wait on Postgres; a few can share the loaded models.
    max_jobs = 4
    job_timeout = SEARCH_JOB_TIMEOUT_SECONDS
    # Results are read once by the waiting API request.
    keep_result = 60
    max_tries = 1
    health_check_interval = 60
    redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379"))
