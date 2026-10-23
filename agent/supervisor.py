"""Run leases and the run supervisor: graph runs survive an API restart (research §R7).

A graph run executes only in the process holding `run:{id}:lease` (`SET NX PX 30000`, value =
the process id, renewed every 10 s). A renewal that finds the key gone or held by another
process marks the lease lost, and the run stops at its next node boundary.

Each API worker runs one `RunSupervisor`. At startup and every 15 s it claims `running` graph
runs without a lease and resumes them from their last checkpoint (`resume_count` > 3 fails the
run with `interrupted_limit`). Under the lock `supervisor:sweep` (60 s) it also expires plans
past `plan_expires_at` and, once a day, deletes the checkpoint threads of runs finished more
than `CHECKPOINT_RETENTION_DAYS` ago.
"""

import asyncio
import contextlib
import logging
import os
import socket
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from agent.budget import utcnow

if TYPE_CHECKING:
    from agent.runs import RunService

logger = logging.getLogger(__name__)

LEASE_TTL_MS = 30_000
RENEW_SECONDS = 10.0
SWEEP_SECONDS = 15.0
SWEEP_LOCK_KEY = "supervisor:sweep"
SWEEP_LOCK_TTL_MS = 60_000
RETENTION_KEY = "supervisor:retention:{day}"
RETENTION_KEY_TTL_SECONDS = 2 * 24 * 3600
MAX_RESUMES = 3


class LeaseRedis(Protocol):
    async def set(
        self, name: str, value: str, *, nx: bool = False, px: int | None = None
    ) -> Any: ...

    async def get(self, name: str) -> Any: ...

    async def pexpire(self, name: str, time: int) -> Any: ...

    async def delete(self, *names: str) -> Any: ...

    async def exists(self, *names: str) -> Any: ...


def lease_key(run_id: uuid.UUID | str) -> str:
    return f"run:{run_id}:lease"


def process_id() -> str:
    """This process's lease value: host, pid and a random part (pids repeat in containers)."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else str(value)


class Lease:
    """One run's lease. `lost` turns true once a renewal finds it gone or taken."""

    def __init__(self, redis: LeaseRedis | None, run_id: uuid.UUID | str, owner: str) -> None:
        self._redis = redis
        self.key = lease_key(run_id)
        self.owner = owner
        self.lost = False

    async def acquire(self, ttl_ms: int = LEASE_TTL_MS) -> bool:
        if self._redis is None:
            return True
        return bool(await self._redis.set(self.key, self.owner, nx=True, px=ttl_ms))

    async def renew(self, ttl_ms: int = LEASE_TTL_MS) -> bool:
        if self._redis is None or self.lost:
            return not self.lost
        if _text(await self._redis.get(self.key)) != self.owner:
            self.lost = True
            return False
        await self._redis.pexpire(self.key, ttl_ms)
        return True

    async def release(self) -> None:
        """Delete the key if this process still holds it."""
        if self._redis is None or self.lost:
            return
        if _text(await self._redis.get(self.key)) == self.owner:
            await self._redis.delete(self.key)

    async def keep(self, every: float = RENEW_SECONDS) -> None:
        """Renew until cancelled or lost (run next to the run task)."""
        while not self.lost:
            await asyncio.sleep(every)
            try:
                await self.renew()
            except Exception:
                # Redis hiccup: keep the run going; the key expires if Redis stays away.
                logger.warning("lease renewal failed", extra={"lease": self.key}, exc_info=True)


class Leases:
    """Lease factory for one process. Without Redis every lease is held (tests, single run)."""

    def __init__(self, redis: LeaseRedis | None, owner: str | None = None) -> None:
        self.redis = redis
        self.owner = owner or process_id()

    def lease(self, run_id: uuid.UUID | str) -> Lease:
        return Lease(self.redis, run_id, self.owner)

    async def claim(self, run_id: uuid.UUID | str) -> Lease | None:
        lease = self.lease(run_id)
        return lease if await lease.acquire() else None

    async def held(self, run_id: uuid.UUID | str) -> bool:
        if self.redis is None:
            return False
        return bool(await self.redis.exists(lease_key(run_id)))


class RunSupervisor:
    """Resumes interrupted graph runs, expires plans and prunes old checkpoints."""

    def __init__(
        self,
        service: "RunService",
        leases: Leases,
        *,
        interval: float = SWEEP_SECONDS,
        max_resumes: int = MAX_RESUMES,
        retention_days: int = 7,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._service = service
        self._leases = leases
        self._interval = interval
        self._max_resumes = max_resumes
        self._retention = timedelta(days=retention_days)
        self._now = now
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.sweep()
            except Exception:
                # A database or Redis outage must not end the loop; the next sweep retries.
                logger.warning("supervisor sweep failed", exc_info=True)
            await asyncio.sleep(self._interval)

    async def sweep(self) -> list[uuid.UUID]:
        """One pass; returns the runs this process resumed."""
        resumed = await self.resume_orphans()
        redis = self._leases.redis
        if redis is None or await redis.set(
            SWEEP_LOCK_KEY, self._leases.owner, nx=True, px=SWEEP_LOCK_TTL_MS
        ):
            await self._service.expire_plans(self._now())
            await self._prune()
        return resumed

    async def resume_orphans(self) -> list[uuid.UUID]:
        resumed: list[uuid.UUID] = []
        for run in await self._service.running_graph_runs():
            if await self._leases.held(run.id):
                continue
            lease = await self._leases.claim(run.id)
            if lease is None:
                continue
            count = await self._service.bump_resume_count(run.id)
            if count > self._max_resumes:
                await self._service.fail_interrupted(run, lease)
                continue
            await self._service.resume_interrupted(run, lease, count)
            resumed.append(run.id)
        return resumed

    async def _prune(self) -> None:
        redis = self._leases.redis
        now = self._now()
        if redis is not None:
            day_key = RETENTION_KEY.format(day=now.date().isoformat())
            if not await redis.set(
                day_key, self._leases.owner, nx=True, px=RETENTION_KEY_TTL_SECONDS * 1000
            ):
                return
        for run_id in await self._service.finished_graph_runs(now - self._retention):
            await self._service.delete_checkpoint(run_id)
