"""Closed-beta allowlist (FR-021, research §R5).

Emails live in the `beta_allowlist` table, lower-cased. Each check is cached in Redis as
`allow:{email_hash}` (`1`/`0`) for 60 s, so adding or removing an email applies within a
minute without a redeploy. The owner manages the list with:

    python -m api.auth.allowlist add|remove EMAIL [--note TEXT]
    python -m api.auth.allowlist list

`list` prints masked emails only.
"""

import argparse
import asyncio
import hashlib
import os
import sys
from collections.abc import Callable
from typing import Any, Protocol

from sqlalchemy import Engine, delete, select, text
from sqlalchemy.dialects.postgresql import insert

from api import schema

CACHE_TTL_SECONDS = 60

_LOOKUP_SQL = text(
    "SELECT 1 FROM beta_allowlist WHERE encode(sha256(convert_to(email, 'UTF8')), 'hex') = :h"
)


class AllowRedis(Protocol):
    async def get(self, name: str) -> Any: ...

    async def set(self, name: str, value: str, ex: int | None = None) -> Any: ...


def normalize_email(email: str) -> str:
    return email.strip().lower()


def email_hash(email: str) -> str:
    return hashlib.sha256(normalize_email(email).encode()).hexdigest()


def mask_email(email: str) -> str:
    local, _, domain = normalize_email(email).partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def cache_key(hashed: str) -> str:
    return f"allow:{hashed}"


def db_lookup(engine: Engine) -> Callable[[str], bool]:
    def lookup(hashed: str) -> bool:
        with engine.connect() as conn:
            return conn.execute(_LOOKUP_SQL, {"h": hashed}).first() is not None

    return lookup


class Allowlist:
    def __init__(self, redis: AllowRedis, lookup: Callable[[str], bool]) -> None:
        self._redis = redis
        self._lookup = lookup

    async def allows_hash(self, hashed: str) -> bool:
        cached = await self._redis.get(cache_key(hashed))
        if cached is not None:
            return (cached.decode() if isinstance(cached, bytes) else str(cached)) == "1"
        allowed = await asyncio.to_thread(self._lookup, hashed)
        await self._redis.set(cache_key(hashed), "1" if allowed else "0", ex=CACHE_TTL_SECONDS)
        return allowed

    async def allows(self, email: str) -> bool:
        return await self.allows_hash(email_hash(email))


def add(engine: Engine, email: str, note: str | None = None) -> None:
    statement = insert(schema.beta_allowlist).values(email=normalize_email(email), note=note)
    with engine.begin() as conn:
        conn.execute(statement.on_conflict_do_nothing(index_elements=["email"]))


def remove(engine: Engine, email: str) -> bool:
    table = schema.beta_allowlist
    with engine.begin() as conn:
        result = conn.execute(delete(table).where(table.c.email == normalize_email(email)))
    return result.rowcount > 0


def list_masked(engine: Engine) -> list[str]:
    table = schema.beta_allowlist
    with engine.connect() as conn:
        emails = conn.execute(select(table.c.email).order_by(table.c.added_at)).scalars()
        return [mask_email(email) for email in emails]


def main(argv: list[str] | None = None, engine: Engine | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m api.auth.allowlist")
    commands = parser.add_subparsers(dest="command", required=True)
    add_parser = commands.add_parser("add")
    add_parser.add_argument("email")
    add_parser.add_argument("--note")
    commands.add_parser("remove").add_argument("email")
    commands.add_parser("list")
    args = parser.parse_args(argv)

    if engine is None:  # pragma: no cover - the CLI path, tests pass an engine
        from ingestion.db import make_engine

        engine = make_engine(os.environ["DATABASE_URL"])
    if args.command == "add":
        if "@" not in args.email:
            print("not an email address", file=sys.stderr)
            return 2
        add(engine, args.email, args.note)
        print(f"added {mask_email(args.email)}")
    elif args.command == "remove":
        found = remove(engine, args.email)
        print(f"removed {mask_email(args.email)}" if found else "not on the list")
        return 0 if found else 1
    else:
        for masked in list_masked(engine):
            print(masked)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
