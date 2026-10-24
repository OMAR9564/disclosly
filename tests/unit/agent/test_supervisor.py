"""Tests for run leases and the run supervisor (Phase 4 T039, research §R7)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from agent.graph.build import build_graph
from agent.graph.context import GraphDeps, NodeContext
from agent.graph.state import ResearchState
from agent.loop import Budget
from agent.runs import InterruptedRun, RunService
from agent.supervisor import (
    LEASE_TTL_MS,
    SWEEP_LOCK_KEY,
    Lease,
    Leases,
    RunSupervisor,
    lease_key,
)
from llm.testing import FakeRouter
from tests.agent_fakes import FakeTools
from tests.unit.tools.test_documents import _registry

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)


class LeaseFakeRedis:
    """SET NX PX, GET, PEXPIRE, DELETE, EXISTS, plus the stream calls RunService makes."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.px: dict[str, int] = {}
        self.events: list[tuple[str, dict[str, str]]] = []

    async def set(self, name: str, value: str, *, nx: bool = False, px: int | None = None) -> Any:
        if nx and name in self.values:
            return None
        self.values[name] = value
        if px is not None:
            self.px[name] = px
        return True

    async def get(self, name: str) -> Any:
        value = self.values.get(name)
        return None if value is None else value.encode()

    async def pexpire(self, name: str, time: int) -> Any:
        self.px[name] = time
        return name in self.values

    async def delete(self, *names: str) -> Any:
        return sum(self.values.pop(name, None) is not None for name in names)

    async def exists(self, *names: str) -> Any:
        return sum(name in self.values for name in names)

    async def xadd(self, name: str, fields: dict[str, str]) -> Any:
        self.events.append((name, fields))
        return "0-1"

    async def expire(self, name: str, time: int) -> Any:
        return True

    async def incr(self, name: str) -> Any:
        self.values[name] = str(int(self.values.get(name, "0")) + 1)
        return int(self.values[name])

    async def xread(
        self, streams: dict[str, str], count: int | None = None, block: int | None = None
    ) -> Any:
        return []


async def test_lease_is_held_by_one_process() -> None:
    redis = LeaseFakeRedis()
    run_id = uuid.uuid4()
    first, second = Leases(redis, owner="a"), Leases(redis, owner="b")

    lease = await first.claim(run_id)
    assert lease is not None
    assert redis.px[lease_key(run_id)] == LEASE_TTL_MS
    assert await second.claim(run_id) is None
    assert await second.held(run_id)

    assert await lease.renew()
    await lease.release()
    assert not await second.held(run_id)
    assert await second.claim(run_id) is not None


async def test_renewal_after_a_takeover_loses_the_lease() -> None:
    redis = LeaseFakeRedis()
    run_id = uuid.uuid4()
    lease = await Leases(redis, owner="a").claim(run_id)
    assert lease is not None
    redis.values[lease_key(run_id)] = "b"  # expired and claimed elsewhere

    assert not await lease.renew()
    assert lease.lost
    await lease.release()  # a lost lease never deletes the new owner's key
    assert redis.values[lease_key(run_id)] == "b"


async def test_keep_stops_once_the_lease_is_lost() -> None:
    redis = LeaseFakeRedis()
    lease = Lease(redis, "r1", "a")
    assert await lease.acquire()
    await redis.delete(lease.key)
    await lease.keep(every=0)
    assert lease.lost


async def test_without_redis_every_lease_is_held() -> None:
    leases = Leases(None, owner="a")
    lease = await leases.claim("r1")
    assert lease is not None
    assert await lease.renew()
    assert not await leases.held("r1")


def _run(run_id: uuid.UUID) -> InterruptedRun:
    return InterruptedRun(
        id=run_id, user_id=uuid.uuid4(), language="tr", decision=None, plan=None, decided_at=None
    )


class FakeRuns:
    """The supervisor's view of RunService."""

    def __init__(self, runs: list[InterruptedRun], resumes: int = 0) -> None:
        self.runs = runs
        self.counts = {run.id: resumes for run in runs}
        self.resumed: list[tuple[uuid.UUID, int]] = []
        self.failed: list[uuid.UUID] = []
        self.expired_at: list[datetime] = []
        self.pruned_before: list[datetime] = []
        self.deleted: list[uuid.UUID] = []

    async def running_graph_runs(self) -> list[InterruptedRun]:
        return [run for run in self.runs if run.id not in self.failed]

    async def bump_resume_count(self, run_id: uuid.UUID) -> int:
        self.counts[run_id] += 1
        return self.counts[run_id]

    async def resume_interrupted(self, run: InterruptedRun, lease: Lease, count: int) -> None:
        self.resumed.append((run.id, count))

    async def fail_interrupted(self, run: InterruptedRun, lease: Lease) -> None:
        self.failed.append(run.id)
        await lease.release()

    async def expire_plans(self, now: datetime) -> list[uuid.UUID]:
        self.expired_at.append(now)
        return []

    async def finished_graph_runs(self, before: datetime) -> list[uuid.UUID]:
        self.pruned_before.append(before)
        return [uuid.UUID(int=7)]

    async def delete_checkpoint(self, run_id: uuid.UUID) -> None:
        self.deleted.append(run_id)


def _supervisor(service: FakeRuns, redis: LeaseFakeRedis, owner: str) -> RunSupervisor:
    return RunSupervisor(
        service,  # type: ignore[arg-type]
        Leases(redis, owner=owner),
        retention_days=7,
        now=lambda: NOW,
    )


async def test_two_supervisors_resume_a_run_once() -> None:
    redis = LeaseFakeRedis()
    run_id = uuid.uuid4()
    service = FakeRuns([_run(run_id)])
    first, second = _supervisor(service, redis, "a"), _supervisor(service, redis, "b")

    assert await first.sweep() == [run_id]
    assert await second.sweep() == []
    assert service.resumed == [(run_id, 1)]
    assert redis.values[lease_key(run_id)] == "a"


async def test_a_held_run_is_left_alone() -> None:
    redis = LeaseFakeRedis()
    run_id = uuid.uuid4()
    await redis.set(lease_key(run_id), "other", nx=True, px=LEASE_TTL_MS)
    service = FakeRuns([_run(run_id)])
    assert await _supervisor(service, redis, "a").resume_orphans() == []
    assert service.counts[run_id] == 0


async def test_fourth_resume_fails_the_run() -> None:
    redis = LeaseFakeRedis()
    run_id = uuid.uuid4()
    service = FakeRuns([_run(run_id)], resumes=3)
    assert await _supervisor(service, redis, "a").resume_orphans() == []
    assert service.failed == [run_id]
    assert service.resumed == []
    assert lease_key(run_id) not in redis.values


async def test_sweep_lock_and_daily_prune() -> None:
    redis = LeaseFakeRedis()
    service = FakeRuns([])
    first, second = _supervisor(service, redis, "a"), _supervisor(service, redis, "b")

    await first.sweep()
    await second.sweep()  # the lock is held: no second expiry pass
    assert service.expired_at == [NOW]
    assert service.pruned_before == [datetime(2026, 10, 16, 9, 0, tzinfo=UTC)]
    assert service.deleted == [uuid.UUID(int=7)]

    await redis.delete(SWEEP_LOCK_KEY)
    await first.sweep()  # same day: plans expire again, checkpoints are not pruned twice
    assert service.expired_at == [NOW, NOW]
    assert len(service.pruned_before) == 1


async def test_lost_lease_stops_the_run_at_the_next_node() -> None:
    redis = LeaseFakeRedis()
    ran: list[str] = []
    # A stopped run never reaches the database.
    service = RunService(
        None,  # type: ignore[arg-type]
        redis,
        FakeRouter(),
        FakeTools(),
        Budget(),
        agent_engine="graph",
        leases=Leases(redis, owner="a"),
    )
    run_id = uuid.uuid4()
    lease = await service.claim(run_id)
    assert lease is not None

    async def router(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
        ran.append("router")
        redis.values[lease.key] = "b"  # another process took the run over
        await lease.renew()
        return {"mode": "simple"}

    async def retriever(state: ResearchState, ctx: NodeContext) -> dict[str, Any]:
        ran.append("retriever_simple")
        return {}

    registry, _ = _registry([])
    deps = GraphDeps(
        router=FakeRouter(), tools=registry, emit=service.emit_event, attempts=service.attempt
    )
    service.attach_graph(
        build_graph(deps, InMemorySaver(), {"router": router, "retriever_simple": retriever})
    )
    state: ResearchState = {
        "run_id": str(run_id),
        "user_id": str(uuid.uuid4()),
        "question": "q",
        "language": "tr",
        "submitted_at": NOW,
    }

    await service._drive(run_id, uuid.uuid4(), "tr", state, lease)

    assert ran == ["router"]
    assert lease.lost
    assert redis.values[lease.key] == "b"
    assert not any(fields["event"] in ("final", "error") for _, fields in redis.events)
