"""Daily usage tracking and quota-aware skipping in llm.router (T026)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from llm.router import USAGE_TTL_SECONDS, ModelRouter, usage_key
from llm.types import ChatMessage, ChatRequest, ProvidersUnavailable
from tests.unit.llm.fakes import FakeStore, config, http

REQUEST = ChatRequest(messages=[ChatMessage(role="user", content="hello")])
NOW = datetime(2026, 10, 21, 23, 30, tzinfo=UTC)


def test_usage_key_uses_the_utc_day() -> None:
    istanbul = NOW.astimezone(timezone(timedelta(hours=3)))  # already 22 Oct there
    assert usage_key("a", istanbul) == "llm:usage:a:20261021"


async def test_each_call_increments_usage_with_ttl() -> None:
    store = FakeStore()
    router = ModelRouter(config("a"), http({}, []), store=store, now=lambda: NOW)

    await router.chat(REQUEST, task_class="cheap")
    await router.chat(REQUEST, task_class="cheap")

    key = usage_key("a", NOW)
    assert store.values[key] == "2"
    assert store.ttls[key] == USAGE_TTL_SECONDS


@pytest.mark.parametrize(("used", "skipped"), [(8, False), (9, True), (10, True)])
async def test_skips_at_ninety_percent_of_daily_requests(used: int, skipped: bool) -> None:
    store, hosts = FakeStore(), list[str]()
    store.values[usage_key("a", NOW)] = str(used)
    router = ModelRouter(config("a", "b"), http({}, hosts), store=store, now=lambda: NOW)

    response = await router.chat(REQUEST, task_class="cheap")

    assert response.provider == ("b" if skipped else "a")
    assert hosts == (["b"] if skipped else ["a"])


async def test_every_provider_near_quota_raises() -> None:
    store = FakeStore()
    for name in ("a", "b"):
        store.values[usage_key(name, NOW)] = "9"
    router = ModelRouter(config("a", "b"), http({}, []), store=store, now=lambda: NOW)

    with pytest.raises(ProvidersUnavailable):
        await router.chat(REQUEST, task_class="cheap")


async def test_new_day_resets_usage() -> None:
    store, hosts = FakeStore(), list[str]()
    store.values[usage_key("a", NOW)] = "10"
    tomorrow = NOW + timedelta(hours=1)
    router = ModelRouter(config("a"), http({}, hosts), store=store, now=lambda: tomorrow)

    await router.chat(REQUEST, task_class="cheap")

    assert hosts == ["a"]
