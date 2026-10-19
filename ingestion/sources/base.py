"""Source protocol and a rate-limited HTTP client shared by KAP and SEC (research §R1-R2)."""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Protocol

import httpx

from ingestion.config import Company
from ingestion.models import DocType, Language

RETRY_STATUSES = frozenset({403, 429, 500, 502, 503, 504})
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_ATTEMPTS = 4
MAX_BACKOFF_S = 60.0


@dataclass(frozen=True)
class RemoteDocument:
    """A document a source can fetch; not yet stored."""

    ticker: str
    source_url: str
    doc_type: DocType
    period: str
    published_at: datetime
    language: Language
    extra: Mapping[str, str] = field(default_factory=dict)


class Source(Protocol):
    async def list_documents(self, company: Company, since: datetime) -> list[RemoteDocument]: ...

    async def fetch(self, remote: RemoteDocument) -> bytes: ...


Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class TokenBucket:
    """Allows `rate` requests per second with bursts up to `capacity`."""

    def __init__(self, rate: float, capacity: float, clock: Clock, sleep: Sleep) -> None:
        if rate <= 0 or capacity < 1:
            raise ValueError("rate must be > 0 and capacity >= 1")
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                elapsed = now - self._updated
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await self._sleep((1 - self._tokens) / self._rate)


class HttpError(Exception):
    """A request failed after all attempts; the message carries no response body."""

    def __init__(self, url: str, status: int | None, attempts: int) -> None:
        reason = f"HTTP {status}" if status is not None else "transport error"
        super().__init__(f"{reason} for {url} after {attempts} attempt(s)")
        self.url = url
        self.status = status
        self.attempts = attempts


class RateLimitedClient:
    """`httpx.AsyncClient` wrapper: per-host token bucket, timeouts, retry with backoff."""

    def __init__(
        self,
        rates: Mapping[str, float],
        *,
        headers: Mapping[str, str] | None = None,
        default_rate: float = 1.0,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._rates = dict(rates)
        self._default_rate = default_rate
        self._max_attempts = max_attempts
        self._clock = clock
        self._sleep = sleep
        self._buckets: dict[str, TokenBucket] = {}
        self._client = httpx.AsyncClient(
            headers=dict(headers or {}),
            timeout=timeout_s,
            transport=transport,
            follow_redirects=True,
        )

    async def __aenter__(self) -> "RateLimitedClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _bucket(self, host: str) -> TokenBucket:
        bucket = self._buckets.get(host)
        if bucket is None:
            rate = self._rates.get(host, self._default_rate)
            bucket = TokenBucket(rate, max(1.0, rate), self._clock, self._sleep)
            self._buckets[host] = bucket
        return bucket

    async def get(self, url: str, params: Mapping[str, str] | None = None) -> httpx.Response:
        return await self._request("GET", url, params=params)

    async def post_json(self, url: str, body: object) -> httpx.Response:
        return await self._request("POST", url, json=body)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        json: object = None,
    ) -> httpx.Response:
        host = httpx.URL(url).host
        status: int | None = None
        for attempt in range(1, self._max_attempts + 1):
            await self._bucket(host).acquire()
            retry_after: float | None = None
            try:
                response = await self._client.request(method, url, params=params, json=json)
            except httpx.TransportError:
                status = None
            else:
                if response.status_code not in RETRY_STATUSES:
                    if response.is_error:
                        raise HttpError(url, response.status_code, attempt)
                    return response
                status = response.status_code
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            if attempt < self._max_attempts:
                await self._sleep(_backoff(attempt, retry_after))
        raise HttpError(url, status, self._max_attempts)


def _backoff(attempt: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return min(retry_after, MAX_BACKOFF_S)
    base: float = 2 ** (attempt - 1)
    return min(base + random.uniform(0, base / 2), MAX_BACKOFF_S)  # noqa: S311 - jitter only


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())
