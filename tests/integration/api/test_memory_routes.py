"""/v1/me/memory and /v1/companies (T046): owner isolation, limits, idempotency."""

import uuid

import pytest
from sqlalchemy import Engine, insert

from agent import memory
from ingestion import schema as corpus
from tests.integration.agent.helpers import add_user
from tests.integration.api.test_me import SETTINGS, _client
from tests.integration.retrieval.corpus import Corpus, load


@pytest.fixture
def corpus_db(engine: Engine) -> Corpus:
    return load(engine)


async def test_memory_round_trip(engine: Engine, corpus_db: Corpus) -> None:
    user = add_user(engine)
    async with _client(engine, user) as client:
        empty = (await client.get("/v1/me/memory")).json()
        assert empty == {
            "watchlist": [],
            "preferences": {"answer_language": None, "default_period_range": None},
            "limits": {"watchlist_max": 50},
        }
        first = await client.post("/v1/me/memory/watchlist", json={"ticker": "thyao"})
        again = await client.post("/v1/me/memory/watchlist", json={"ticker": "THYAO"})
        assert first.status_code == again.status_code == 200
        assert [i["ticker"] for i in again.json()["watchlist"]] == ["THYAO"]
        item = again.json()["watchlist"][0]
        assert (item["name"], item["market"]) == ("Türk Hava Yolları", "bist")

        prefs = await client.put(
            "/v1/me/memory/preferences",
            json={"answer_language": "en", "default_period_range": "last_2fy"},
        )
        assert prefs.json()["preferences"] == {
            "answer_language": "en",
            "default_period_range": "last_2fy",
        }

        removed = await client.delete("/v1/me/memory/watchlist/THYAO")
        missing = await client.delete("/v1/me/memory/watchlist/THYAO")
        assert removed.status_code == missing.status_code == 200
        assert missing.json()["watchlist"] == []

        assert (await client.delete("/v1/me/memory")).status_code == 204
        assert (await client.get("/v1/me/memory")).json() == empty


async def test_errors(engine: Engine, corpus_db: Corpus) -> None:
    user = add_user(engine)
    async with _client(engine, user) as client:
        unknown = await client.post("/v1/me/memory/watchlist", json={"ticker": "ZZZZ"})
        assert unknown.status_code == 422
        assert unknown.json()["error"]["code"] == "company_not_covered"
        bad = await client.put("/v1/me/memory/preferences", json={"default_period_range": "5y"})
        assert bad.status_code == 422
        assert bad.json()["error"]["code"] == "validation_error"

        with engine.begin() as conn:
            conn.execute(
                insert(corpus.companies),
                [
                    {"id": 1000 + i, "ticker": f"T{i:03d}", "name": f"Co {i}", "market": "bist",
                     "kap_member_id": f"k{i}"}
                    for i in range(50)
                ],
            )  # fmt: skip
        for i in range(50):
            memory.add_to_watchlist(engine, user, f"T{i:03d}")
        full = await client.post("/v1/me/memory/watchlist", json={"ticker": "THYAO"})
        assert full.status_code == 422
        assert full.json()["error"]["code"] == "watchlist_full"


async def test_owner_isolation(engine: Engine, corpus_db: Corpus) -> None:
    owner = add_user(engine)
    other = add_user(engine, "other@example.test")
    memory.add_to_watchlist(engine, owner, "AAPL")
    async with _client(engine, other) as client:
        assert (await client.get("/v1/me/memory")).json()["watchlist"] == []
        await client.delete("/v1/me/memory")
    assert memory.read_snapshot(engine, owner).watchlist == ["AAPL"]


async def test_unauthenticated_requests_are_refused(engine: Engine, corpus_db: Corpus) -> None:
    async with _client(engine, uuid.uuid4()) as client:
        client.cookies.clear()
        for method, path in [("GET", "/v1/me/memory"), ("GET", "/v1/companies")]:
            response = await client.request(method, path)
            assert response.status_code == 401


async def test_company_search(engine: Engine, corpus_db: Corpus) -> None:
    async with _client(engine, add_user(engine)) as client:
        found = await client.get("/v1/companies", params={"q": "yolları"})
        assert found.json() == {
            "items": [{"ticker": "THYAO", "name": "Türk Hava Yolları", "market": "bist"}]
        }
        assert len((await client.get("/v1/companies", params={"limit": 1})).json()["items"]) == 1
        for params in ({"q": ""}, {"q": "x" * 51}, {"limit": 0}, {"limit": 21}):
            response = await client.get("/v1/companies", params=params)
            assert response.status_code == 422, params


async def test_rate_limited(engine: Engine, corpus_db: Corpus) -> None:
    async with _client(engine, add_user(engine), user_per_min=2) as client:
        codes = [(await client.get("/v1/companies")).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


async def test_cors_allows_delete(engine: Engine, corpus_db: Corpus) -> None:
    async with _client(engine, add_user(engine)) as client:
        response = await client.options(
            "/v1/me/memory",
            headers={
                "Origin": SETTINGS.cors_allowed_origins[0],
                "Access-Control-Request-Method": "DELETE",
            },
        )
    assert response.status_code == 200
    assert "DELETE" in response.headers["access-control-allow-methods"]
