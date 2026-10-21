"""Document search through the search worker (workers/search.py).

The API image carries no ML models: each search is an arq job on the `arq:search` queue, and
the request waits for its result. The Redis pool is opened on first use.
"""

from typing import Any, Protocol

from arq import create_pool
from arq.connections import RedisSettings

from ingestion.models import DocType
from retrieval.types import DocumentMeta, SearchFilters, SearchHit

SEARCH_QUEUE = "arq:search"
RESULT_TIMEOUT_SECONDS = 25.0


class SearchUnavailable(RuntimeError):
    """The search worker did not answer in time or failed."""


class JobPool(Protocol):
    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> Any: ...

    async def aclose(self) -> None: ...


class RemoteSearch:
    def __init__(
        self,
        redis_url: str,
        *,
        pool: JobPool | None = None,
        timeout: float = RESULT_TIMEOUT_SECONDS,
    ) -> None:
        self._redis_url = redis_url
        self._pool = pool
        self._timeout = timeout

    async def _job(self, function: str, *args: Any) -> list[dict[str, Any]]:
        if self._pool is None:
            self._pool = await create_pool(RedisSettings.from_dsn(self._redis_url))
        job = await self._pool.enqueue_job(function, *args, _queue_name=SEARCH_QUEUE)
        if job is None:  # pragma: no cover - only for a duplicate job id, which we never set
            raise SearchUnavailable(function)
        try:
            result = await job.result(timeout=self._timeout)
        except Exception as exc:
            raise SearchUnavailable(function) from exc
        if not isinstance(result, list):
            raise SearchUnavailable(function)
        return result

    async def search(
        self, query: str, filters: SearchFilters | None = None, k: int = 6
    ) -> list[SearchHit]:
        payload = filters.model_dump(mode="json", exclude_none=True) if filters else None
        rows = await self._job("search", query, payload, k)
        return [SearchHit.model_validate(row) for row in rows]

    async def list_documents(
        self,
        ticker: str,
        doc_types: list[DocType] | None = None,
        period_from: str | None = None,
        period_to: str | None = None,
    ) -> list[DocumentMeta]:
        types = [d.value for d in doc_types] if doc_types is not None else None
        rows = await self._job("list_documents", ticker, types, period_from, period_to)
        return [DocumentMeta.model_validate(row) for row in rows]

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
