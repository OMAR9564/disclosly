"""agent.memory (T045): watchlist and preferences on a migrated database."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import Engine, insert

from agent import memory
from ingestion import schema as corpus
from tests.integration.agent.helpers import add_user
from tests.integration.retrieval.corpus import Corpus, load


@pytest.fixture
def corpus_db(engine: Engine) -> Corpus:
    return load(engine)


def test_add_read_remove_watchlist(engine: Engine, corpus_db: Corpus) -> None:
    user = add_user(engine)
    assert memory.read_snapshot(engine, user).watchlist == []

    assert memory.add_to_watchlist(engine, user, "thyao") is True
    assert memory.add_to_watchlist(engine, user, "THYAO") is False  # idempotent
    assert memory.add_to_watchlist(engine, user, "AAPL") is True
    assert memory.read_snapshot(engine, user).watchlist == ["THYAO", "AAPL"]

    view = memory.read_memory(engine, user)
    assert [(i["ticker"], i["market"]) for i in view["watchlist"]] == [
        ("THYAO", "bist"),
        ("AAPL", "us"),
    ]
    assert view["limits"] == {"watchlist_max": 50}

    assert memory.remove_from_watchlist(engine, user, "thyao") is True
    assert memory.remove_from_watchlist(engine, user, "THYAO") is False
    assert memory.remove_from_watchlist(engine, user, "NOPE") is False
    assert memory.read_snapshot(engine, user).watchlist == ["AAPL"]


def test_unknown_ticker_is_not_covered(engine: Engine, corpus_db: Corpus) -> None:
    user = add_user(engine)
    with pytest.raises(memory.CompanyNotCovered) as exc:
        memory.add_to_watchlist(engine, user, "ZZZZ")
    assert exc.value.code == "company_not_covered"
    assert memory.read_snapshot(engine, user).watchlist == []


def _fill(engine: Engine, count: int) -> list[str]:
    tickers = [f"T{i:03d}" for i in range(count)]
    with engine.begin() as conn:
        conn.execute(
            insert(corpus.companies),
            [
                {"id": 1000 + i, "ticker": t, "name": f"Co {t}", "market": "bist",
                 "kap_member_id": f"k{i}"}
                for i, t in enumerate(tickers)
            ],
        )  # fmt: skip
    return tickers


def test_watchlist_is_capped_at_50(engine: Engine, corpus_db: Corpus) -> None:
    user = add_user(engine)
    tickers = _fill(engine, 50)
    for ticker in tickers:
        memory.add_to_watchlist(engine, user, ticker)
    # A present ticker stays idempotent at the limit; a new one is refused.
    assert memory.add_to_watchlist(engine, user, tickers[0]) is False
    with pytest.raises(memory.WatchlistFull):
        memory.add_to_watchlist(engine, user, "THYAO")
    assert len(memory.read_snapshot(engine, user).watchlist) == 50


async def test_concurrent_adds_do_not_pass_the_cap(engine: Engine, corpus_db: Corpus) -> None:
    user = add_user(engine)
    tickers = _fill(engine, 52)
    for ticker in tickers[:49]:
        memory.add_to_watchlist(engine, user, ticker)

    def add(ticker: str) -> str:
        try:
            memory.add_to_watchlist(engine, user, ticker)
            return "added"
        except memory.WatchlistFull:
            return "full"

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(3) as pool:
        results = await asyncio.gather(*(loop.run_in_executor(pool, add, t) for t in tickers[49:]))
    assert sorted(results) == ["added", "full", "full"]
    assert len(memory.read_snapshot(engine, user).watchlist) == 50


def test_preferences_and_delete_all(engine: Engine, corpus_db: Corpus) -> None:
    user = add_user(engine)
    other = add_user(engine, "other@example.test")
    memory.set_preferences(engine, user, "en", "last_2fy")
    memory.set_preferences(engine, user, None, "last_8q")
    memory.add_to_watchlist(engine, user, "THYAO")
    memory.add_to_watchlist(engine, other, "AAPL")

    snapshot = memory.read_snapshot(engine, user)
    assert (snapshot.answer_language, snapshot.default_period_range) == (None, "last_8q")
    # Another user's memory is separate.
    assert memory.read_snapshot(engine, other).watchlist == ["AAPL"]

    memory.delete_all(engine, user)
    assert memory.read_snapshot(engine, user) == memory.read_snapshot(
        engine, add_user(engine, "fresh@example.test")
    )
    assert memory.read_snapshot(engine, other).watchlist == ["AAPL"]


def test_search_companies_folds_case_and_turkish_i(engine: Engine, corpus_db: Corpus) -> None:
    assert [c["ticker"] for c in memory.search_companies(engine, "thy", 10)] == ["THYAO"]
    assert [c["ticker"] for c in memory.search_companies(engine, "YOLLARI", 10)] == ["THYAO"]
    assert [c["ticker"] for c in memory.search_companies(engine, "yolları", 10)] == ["THYAO"]
    assert [c["ticker"] for c in memory.search_companies(engine, "apple", 10)] == ["AAPL"]
    assert len(memory.search_companies(engine, None, 1)) == 1
    assert memory.search_companies(engine, "%", 10) == []


def test_latest_period(engine: Engine, facts_corpus: Corpus) -> None:
    assert memory.latest_period(engine, ["thyao", "AAPL"]) == "2025-Q3"
    assert memory.latest_period(engine, []) is None
    assert memory.latest_period(engine, ["ZZZZ"]) is None


@pytest.mark.parametrize(
    ("period_range", "latest", "expected"),
    [
        (None, "2025-Q3", ["2024-Q4", "2025-Q1", "2025-Q2", "2025-Q3"]),
        ("last_4q", "2025-FY", ["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"]),
        ("last_8q", "2025-Q1", [
            "2023-Q2", "2023-Q3", "2023-Q4", "2024-Q1", "2024-Q2", "2024-Q3", "2024-Q4", "2025-Q1",
        ]),
        ("last_2fy", "2025-Q3", ["2023-FY", "2024-FY"]),
        ("last_3fy", "2025-FY", ["2023-FY", "2024-FY", "2025-FY"]),
        ("last_4q", None, []),
    ],
)  # fmt: skip
def test_resolve_periods(period_range: str | None, latest: str | None, expected: list[str]) -> None:
    assert memory.resolve_periods(period_range, latest) == expected  # type: ignore[arg-type]


def test_latest_period_prefers_a_quarter_to_a_mid_year_fiscal_year(
    engine: Engine, facts_corpus: Corpus
) -> None:
    # A fiscal year ending in June 2026 must not send the default range to 2026-Q4.
    with engine.begin() as conn:
        conn.execute(
            insert(corpus.financial_facts),
            [{"id": 602, "company_id": 2, "metric": "revenue", "period": "2026-FY",
              "value": Decimal("280"), "unit": "billion", "currency": "USD",
              "source_document_id": 22, "source_ref": "Item 8"}],
        )  # fmt: skip
    assert memory.latest_period(engine, ["AAPL"]) == "2025-Q3"
    assert memory.resolve_periods("last_4q", memory.latest_period(engine, ["AAPL"]))[-1] == (
        "2025-Q3"
    )
