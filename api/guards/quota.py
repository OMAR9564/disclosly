"""Daily question quota (FR-020, SC-007, research §R6).

`quota:{user_id}:{yyyymmdd}` counts questions per Europe/Istanbul day and lives for 48 h. The
check and the increment run in one Lua script, so concurrent tabs cannot both take the last
question. A run that fails before its first model call gets its question back (`refund`).
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from api.errors import ApiError

QUOTA_TZ = ZoneInfo("Europe/Istanbul")
QUOTA_TTL_SECONDS = 48 * 3600

# Returns the new count, or -1 when the limit is already reached (nothing is incremented).
_TAKE = """
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
if used >= tonumber(ARGV[1]) then
  return -1
end
used = redis.call('INCR', KEYS[1])
if used == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
end
return used
"""

# Never goes below zero, so a late refund after midnight cannot hand out extra questions.
_REFUND = """
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
if used > 0 then
  return redis.call('DECR', KEYS[1])
end
return 0
"""


class QuotaRedis(Protocol):
    async def get(self, name: str) -> Any: ...

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any: ...


class QuotaStatus(BaseModel):
    limit: int
    used: int
    remaining: int
    resets_at: datetime


def quota_day(now: datetime) -> str:
    return f"{now.astimezone(QUOTA_TZ):%Y%m%d}"


def quota_key(user_id: uuid.UUID, now: datetime) -> str:
    return f"quota:{user_id}:{quota_day(now)}"


def resets_at(now: datetime) -> datetime:
    local = now.astimezone(QUOTA_TZ)
    midnight = datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=QUOTA_TZ)
    return midnight.astimezone(UTC)


def reset_label(at: datetime, language: str) -> str:
    """The reset time shown in the `quota_exhausted` message, in Istanbul time."""
    local = at.astimezone(QUOTA_TZ)
    return f"{local:%H:%M} (TRT)" if language == "en" else f"{local:%H:%M} (TSİ)"


class Quota:
    def __init__(
        self,
        redis: QuotaRedis,
        limit: int,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._redis = redis
        self._limit = limit
        self._now = now

    def _status(self, used: int, now: datetime) -> QuotaStatus:
        used = min(max(used, 0), self._limit)
        return QuotaStatus(
            limit=self._limit, used=used, remaining=self._limit - used, resets_at=resets_at(now)
        )

    async def status(self, user_id: uuid.UUID) -> QuotaStatus:
        now = self._now()
        return self._status(int(await self._redis.get(quota_key(user_id, now)) or 0), now)

    async def take(self, user_id: uuid.UUID, language: str = "tr") -> QuotaStatus:
        """Uses one question or raises `quota_exhausted` with the reset time."""
        now = self._now()
        used = int(
            await self._redis.eval(
                _TAKE, 1, quota_key(user_id, now), self._limit, QUOTA_TTL_SECONDS
            )
        )
        if used < 0:
            at = resets_at(now)
            raise ApiError("quota_exhausted", params={"resets_at": reset_label(at, language)})
        return self._status(used, now)

    async def refund(self, user_id: uuid.UUID, day_of: datetime) -> None:
        await self._redis.eval(_REFUND, 1, quota_key(user_id, day_of))
