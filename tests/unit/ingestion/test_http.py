from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from ingestion.sources.base import (
    MAX_BACKOFF_S,
    HttpError,
    RateLimitedClient,
    TokenBucket,
    _parse_retry_after,
)


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _client(handler: httpx.MockTransport, fake: FakeTime, **kwargs: object) -> RateLimitedClient:
    return RateLimitedClient(
        {"data.sec.gov": 5.0},
        headers={"User-Agent": "Disclosly ops@example.com"},
        transport=handler,
        clock=fake.clock,
        sleep=fake.sleep,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_success_sends_headers_and_params() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    fake = FakeTime()
    async with _client(httpx.MockTransport(handle), fake) as client:
        response = await client.get("https://data.sec.gov/x.json", params={"a": "1"})
    assert response.json() == {"ok": True}
    assert seen[0].headers["User-Agent"] == "Disclosly ops@example.com"
    assert seen[0].url.params["a"] == "1"


async def test_rate_limit_per_host() -> None:
    fake = FakeTime()
    times: list[tuple[str, float]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        times.append((request.url.host, fake.now))
        return httpx.Response(200)

    async with _client(httpx.MockTransport(handle), fake) as client:
        for _ in range(10):
            await client.get("https://data.sec.gov/a")
        for _ in range(3):
            await client.get("https://www.kap.org.tr/a")
    sec = [t for host, t in times if host == "data.sec.gov"]
    kap = [t for host, t in times if host == "www.kap.org.tr"]
    # Burst of 5, then one request per 0.2 s.
    assert sec[4] == pytest.approx(0.0)
    assert sec[9] == pytest.approx(1.0)
    # Default rate 1 req/s for unknown hosts.
    assert kap[2] - kap[0] == pytest.approx(2.0)


@pytest.mark.parametrize("status", [403, 429, 500, 503])
async def test_retries_then_succeeds(status: int) -> None:
    calls = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status) if calls < 3 else httpx.Response(200)

    fake = FakeTime()
    async with _client(httpx.MockTransport(handle), fake) as client:
        response = await client.get("https://data.sec.gov/a")
    assert response.status_code == 200
    assert calls == 3
    backoffs = [s for s in fake.sleeps if s >= 1]
    assert 1 <= backoffs[0] <= 1.5
    assert 2 <= backoffs[1] <= 3


async def test_gives_up_after_max_attempts() -> None:
    calls = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, text="secret body")

    fake = FakeTime()
    async with _client(httpx.MockTransport(handle), fake) as client:
        with pytest.raises(HttpError) as excinfo:
            await client.get("https://data.sec.gov/a")
    assert calls == 4
    assert excinfo.value.status == 503
    assert excinfo.value.attempts == 4
    assert "secret body" not in str(excinfo.value)


async def test_honors_retry_after_seconds() -> None:
    calls = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200)

    fake = FakeTime()
    async with _client(httpx.MockTransport(handle), fake) as client:
        await client.get("https://data.sec.gov/a")
    assert 7.0 in fake.sleeps


async def test_retry_after_is_capped() -> None:
    def handle(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "3600"})

    fake = FakeTime()
    async with _client(httpx.MockTransport(handle), fake, max_attempts=2) as client:
        with pytest.raises(HttpError):
            await client.get("https://data.sec.gov/a")
    assert MAX_BACKOFF_S in fake.sleeps


async def test_non_retryable_error_fails_fast() -> None:
    calls = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404)

    fake = FakeTime()
    async with _client(httpx.MockTransport(handle), fake) as client:
        with pytest.raises(HttpError, match="HTTP 404"):
            await client.get("https://data.sec.gov/a")
    assert calls == 1


async def test_transport_error_retried() -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectTimeout("timeout", request=request)
        return httpx.Response(200)

    fake = FakeTime()
    async with _client(httpx.MockTransport(handle), fake) as client:
        assert (await client.get("https://data.sec.gov/a")).status_code == 200


async def test_transport_error_exhausted() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    fake = FakeTime()
    async with _client(httpx.MockTransport(handle), fake, max_attempts=2) as client:
        with pytest.raises(HttpError, match="transport error"):
            await client.get("https://data.sec.gov/a")


def test_default_timeout() -> None:
    client = RateLimitedClient({})
    assert client._client.timeout.read == 30.0


def test_parse_retry_after() -> None:
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("12") == 12.0
    assert _parse_retry_after("not a date") is None
    future = datetime.now(UTC) + timedelta(seconds=30)
    assert 25 <= (_parse_retry_after(format_datetime(future, usegmt=True)) or 0) <= 31
    past = datetime.now(UTC) - timedelta(seconds=30)
    assert _parse_retry_after(format_datetime(past, usegmt=True)) == 0.0


def test_token_bucket_rejects_bad_rate() -> None:
    fake = FakeTime()
    with pytest.raises(ValueError, match="rate"):
        TokenBucket(0, 1, fake.clock, fake.sleep)
