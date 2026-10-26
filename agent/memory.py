"""User memory: the watchlist and preferences (research §R13, data-model.md).

Memory is read in code (the router reads one snapshot per run) and written only from the
user's own requests: the settings API or a `memory_intent` in the user's question. Retrieved
text never reaches these functions. Writes are idempotent. The watchlist holds corpus
companies only, at most `WATCHLIST_MAX` per user, checked in the insert transaction under a
lock on the user's row so two concurrent adds cannot pass the limit together.

Functions are synchronous (SQLAlchemy Core); async callers use `asyncio.to_thread`.
"""

import asyncio
import uuid
from typing import Any, Literal, get_args

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from agent.answer import Language
from agent.graph.state import MemoryIntent, MemorySnapshot, PeriodRange
from api import schema
from ingestion import schema as corpus
from ingestion.models import Period

WATCHLIST_MAX = 50
DEFAULT_RANGE: PeriodRange = "last_4q"
PERIOD_RANGES: tuple[PeriodRange, ...] = get_args(PeriodRange)


RejectionCode = Literal["company_not_covered", "watchlist_full"]


class MemoryRejected(Exception):
    """A memory change the rules refuse; `code` is the stable API error code."""

    def __init__(self, code: RejectionCode) -> None:
        super().__init__(code)
        self.code = code


class CompanyNotCovered(MemoryRejected):
    def __init__(self) -> None:
        super().__init__("company_not_covered")


class WatchlistFull(MemoryRejected):
    def __init__(self) -> None:
        super().__init__("watchlist_full")


def _watchlist_rows(conn: Any, user_id: uuid.UUID) -> list[Any]:
    return list(
        conn.execute(
            select(
                corpus.companies.c.ticker,
                corpus.companies.c.name,
                corpus.companies.c.market,
                schema.watchlist_items.c.added_at,
            )
            .join(corpus.companies, corpus.companies.c.id == schema.watchlist_items.c.company_id)
            .where(schema.watchlist_items.c.user_id == user_id)
            .order_by(schema.watchlist_items.c.added_at, corpus.companies.c.ticker)
        )
    )


def _preferences(conn: Any, user_id: uuid.UUID) -> tuple[Language | None, PeriodRange | None]:
    row = conn.execute(
        select(
            schema.user_preferences.c.answer_language,
            schema.user_preferences.c.default_period_range,
        ).where(schema.user_preferences.c.user_id == user_id)
    ).first()
    return (row.answer_language, row.default_period_range) if row else (None, None)


def read_snapshot(engine: Engine, user_id: uuid.UUID | str) -> MemorySnapshot:
    """What the graph sees: watchlist tickers and preferences."""
    uid = uuid.UUID(str(user_id))
    with engine.connect() as conn:
        tickers = [row.ticker for row in _watchlist_rows(conn, uid)]
        language, period_range = _preferences(conn, uid)
    return MemorySnapshot(
        watchlist=tickers, answer_language=language, default_period_range=period_range
    )


def read_memory(engine: Engine, user_id: uuid.UUID) -> dict[str, Any]:
    """The API's `Memory` object (contracts/http-api.md)."""
    with engine.connect() as conn:
        rows = _watchlist_rows(conn, user_id)
        language, period_range = _preferences(conn, user_id)
    return {
        "watchlist": [
            {
                "ticker": r.ticker,
                "name": r.name,
                "market": r.market,
                "added_at": r.added_at.isoformat(),
            }
            for r in rows
        ],
        "preferences": {"answer_language": language, "default_period_range": period_range},
        "limits": {"watchlist_max": WATCHLIST_MAX},
    }


def _company_id(conn: Any, ticker: str) -> int:
    company_id = conn.execute(
        select(corpus.companies.c.id).where(corpus.companies.c.ticker == ticker.strip().upper())
    ).scalar_one_or_none()
    if company_id is None:
        raise CompanyNotCovered
    return int(company_id)


def add_to_watchlist(engine: Engine, user_id: uuid.UUID | str, ticker: str) -> bool:
    """Add a corpus company; True when a row was added, False when it was already there."""
    uid = uuid.UUID(str(user_id))
    with engine.begin() as conn:
        company_id = _company_id(conn, ticker)
        # Serialize this user's adds so the count below stays true until commit.
        conn.execute(select(schema.users.c.id).where(schema.users.c.id == uid).with_for_update())
        items = schema.watchlist_items
        present = conn.execute(
            select(items.c.company_id).where(
                items.c.user_id == uid, items.c.company_id == company_id
            )
        ).first()
        if present is not None:
            return False
        count = conn.execute(
            select(func.count()).select_from(items).where(items.c.user_id == uid)
        ).scalar_one()
        if count >= WATCHLIST_MAX:
            raise WatchlistFull
        conn.execute(
            pg_insert(items)
            .values(user_id=uid, company_id=company_id)
            .on_conflict_do_nothing(index_elements=["user_id", "company_id"])
        )
    return True


def remove_from_watchlist(engine: Engine, user_id: uuid.UUID | str, ticker: str) -> bool:
    """Remove a company; True when a row was removed. Unknown tickers are a no-op."""
    uid = uuid.UUID(str(user_id))
    items = schema.watchlist_items
    with engine.begin() as conn:
        company_ids = select(corpus.companies.c.id).where(
            corpus.companies.c.ticker == ticker.strip().upper()
        )
        result = conn.execute(
            delete(items).where(items.c.user_id == uid, items.c.company_id.in_(company_ids))
        )
    return bool(result.rowcount)


def set_preferences(
    engine: Engine,
    user_id: uuid.UUID,
    answer_language: Language | None,
    default_period_range: PeriodRange | None,
) -> None:
    prefs = schema.user_preferences
    values = {"answer_language": answer_language, "default_period_range": default_period_range}
    with engine.begin() as conn:
        conn.execute(
            pg_insert(prefs)
            .values(user_id=user_id, **values)
            .on_conflict_do_update(
                index_elements=["user_id"], set_={**values, "updated_at": func.now()}
            )
        )


def delete_all(engine: Engine, user_id: uuid.UUID) -> None:
    """ "Delete all memory": the watchlist rows and the preferences row."""
    with engine.begin() as conn:
        conn.execute(
            delete(schema.watchlist_items).where(schema.watchlist_items.c.user_id == user_id)
        )
        conn.execute(
            delete(schema.user_preferences).where(schema.user_preferences.c.user_id == user_id)
        )


class EngineMemory:
    """`MemoryStore` over the database, for the graph (async wrappers)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def snapshot(self, user_id: str) -> MemorySnapshot:
        return await asyncio.to_thread(read_snapshot, self._engine, user_id)

    async def apply(self, user_id: str, intent: MemoryIntent) -> str:
        """Run one watchlist change; returns its outcome or the rejection code."""
        change = add_to_watchlist if intent.action == "watchlist_add" else remove_from_watchlist
        try:
            changed = await asyncio.to_thread(change, self._engine, user_id, intent.ticker)
        except MemoryRejected as exc:
            return exc.code
        if not changed:
            return "unchanged"
        return "added" if intent.action == "watchlist_add" else "removed"

    async def latest_period(self, tickers: list[str]) -> str | None:
        return await asyncio.to_thread(latest_period, self._engine, tickers)


def resolve_periods(period_range: PeriodRange | None, latest_available: str | None) -> list[str]:
    """The periods a range covers, oldest first, ending at the latest available period.

    Quarter ranges end at the latest quarter (a latest full year counts as its Q4); year
    ranges end at the latest full year (the year before when the latest period is a quarter).
    """
    if latest_available is None:
        return []
    latest = Period.parse(latest_available)
    chosen = period_range or DEFAULT_RANGE
    if chosen in ("last_4q", "last_8q"):
        count = 4 if chosen == "last_4q" else 8
        year, quarter = latest.year, latest.quarter or 4
        periods = []
        for _ in range(count):
            periods.append(f"{year}-Q{quarter}")
            year, quarter = (year, quarter - 1) if quarter > 1 else (year - 1, 4)
        return periods[::-1]
    count = 2 if chosen == "last_2fy" else 3
    last_year = latest.year if latest.quarter is None else latest.year - 1
    return [f"{year}-FY" for year in range(last_year - count + 1, last_year + 1)]


def latest_period(engine: Engine, tickers: list[str]) -> str | None:
    """The latest period with financial facts for any of `tickers` (None when there are none).

    A quarter wins over a fiscal year: a fiscal year that ends mid-year (Microsoft's ends in
    June) would otherwise count as that year's Q4 and send the default range to quarters that
    have not been reported yet."""
    if not tickers:
        return None
    with engine.connect() as conn:
        periods = conn.execute(
            select(corpus.financial_facts.c.period)
            .distinct()
            .join(corpus.companies, corpus.companies.c.id == corpus.financial_facts.c.company_id)
            .where(corpus.companies.c.ticker.in_([t.upper() for t in tickers]))
        ).scalars()
        parsed = [Period.parse(p) for p in periods]
    quarters = [p for p in parsed if p.quarter is not None]
    return str(max(quarters or parsed)) if parsed else None


def search_companies(engine: Engine, query: str | None, limit: int) -> list[dict[str, Any]]:
    """Corpus companies by ticker prefix or name substring, case- and dotless-i-insensitive."""
    columns = (corpus.companies.c.ticker, corpus.companies.c.name, corpus.companies.c.market)
    statement = select(*columns)
    if query:
        needle = fold(query)
        # ı and İ fold to i; the combining dot that lower('İ') may leave is dropped.
        name = func.lower(func.translate(corpus.companies.c.name, "ıİ\u0307", "ii"))
        statement = statement.where(
            func.lower(corpus.companies.c.ticker).startswith(needle, autoescape=True)
            | name.contains(needle, autoescape=True)
        )
    statement = statement.order_by(corpus.companies.c.ticker).limit(limit)
    with engine.connect() as conn:
        return [
            {"ticker": r.ticker, "name": r.name, "market": r.market}
            for r in conn.execute(statement)
        ]


def fold(text: str) -> str:
    """Lowercase with the Turkish dotted/dotless i folded to "i"."""
    return text.replace("İ", "i").replace("I", "i").lower().replace("ı", "i").replace("i̇", "i")


__all__ = [
    "PERIOD_RANGES",
    "WATCHLIST_MAX",
    "CompanyNotCovered",
    "EngineMemory",
    "MemoryRejected",
    "WatchlistFull",
    "add_to_watchlist",
    "delete_all",
    "latest_period",
    "read_memory",
    "read_snapshot",
    "remove_from_watchlist",
    "resolve_periods",
    "search_companies",
    "set_preferences",
]
