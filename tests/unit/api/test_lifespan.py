"""Tests for the supervisor wiring in the app lifespan (Phase 4 T040, research §R7)."""

import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent.supervisor import Leases, RunSupervisor
from api.config import Settings
from api.main import create_app

SETTINGS = Settings(
    app_env="test",
    database_url="postgresql://u:p@localhost:5432/db",
    redis_url="redis://localhost:6379/0",
    cors_allowed_origins=["https://disclosly.com"],
    jwt_secret="jwt-secret-value-that-is-long-enough-123",  # noqa: S106
    checkpoint_retention_days=9,
)


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    yield
    root.handlers = saved_handlers
    root.setLevel(saved_level)


class GraphService:
    """A service with a graph attached: the lifespan starts a supervisor for it."""

    def __init__(self, graph: object | None) -> None:
        self.engine = object()
        self.graph = graph
        self.leases = Leases(None, owner="test")
        self.calls: list[str] = []

    async def running_graph_runs(self) -> list[Any]:
        self.calls.append("sweep")
        return []

    async def expire_plans(self, now: Any) -> list[Any]:
        return []

    async def finished_graph_runs(self, before: Any) -> list[Any]:
        return []

    async def cancel_graph_runs(self) -> None:
        self.calls.append("cancel")

    async def wait_idle(self) -> None:
        self.calls.append("drain")


def test_supervisor_sweeps_at_startup_and_shutdown_cancels_graph_runs(monkeypatch: Any) -> None:
    started: list[Any] = []
    real_start = RunSupervisor.start

    def start(self: Any) -> None:
        started.append(self)
        real_start(self)

    monkeypatch.setattr("api.main.RunSupervisor.start", start)
    monkeypatch.setattr("api.main.mark_interrupted", lambda engine: 0)
    service = GraphService(graph=object())
    with TestClient(create_app(SETTINGS, run_service=service)):  # type: ignore[arg-type]
        pass
    assert len(started) == 1
    assert started[0]._retention.days == 9
    # The first sweep runs at startup; on shutdown graph runs stop before the drain.
    assert service.calls[0] == "sweep"
    assert service.calls[-2:] == ["cancel", "drain"]


def test_no_supervisor_without_a_graph(monkeypatch: Any) -> None:
    started: list[Any] = []
    monkeypatch.setattr("api.main.RunSupervisor.start", lambda self: started.append(self))
    monkeypatch.setattr("api.main.mark_interrupted", lambda engine: 0)
    service = GraphService(graph=None)
    with TestClient(create_app(SETTINGS, run_service=service)):  # type: ignore[arg-type]
        pass
    assert started == []
    assert service.calls == ["cancel", "drain"]
