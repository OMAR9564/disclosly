"""Tests for api.runtime and the app lifespan (T019 wiring)."""

import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from api.config import Settings
from api.main import create_app
from api.runtime import budget_from, build_guards, build_run_service, graph_deps, graph_runtime
from tools.registry import ToolRegistry

SETTINGS = Settings(
    app_env="test",
    database_url="postgresql://u:p@localhost:5432/db",
    redis_url="redis://localhost:6379/0",
    cors_allowed_origins=["https://disclosly.com"],
    jwt_secret="jwt-secret-value-that-is-long-enough-123",  # noqa: S106
    agent_max_model_calls=3,
    agent_max_tool_calls=2,
    agent_deadline_seconds=30,
)


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """create_app() installs its JSON handler on the root logger; undo it after each test."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    yield
    root.handlers = saved_handlers
    root.setLevel(saved_level)


def test_budget_from_settings() -> None:
    budget = budget_from(SETTINGS)
    assert (budget.max_model_calls, budget.max_tool_calls, budget.deadline_seconds) == (3, 2, 30)


async def test_build_run_service_without_connecting() -> None:
    service, resources = build_run_service(SETTINGS)
    assert isinstance(service._tools, ToolRegistry)
    assert len(resources) == 4
    for resource in resources:
        close = getattr(resource, "aclose", None)
        if close is not None:
            await close()
        else:
            resource.dispose()


class StubService:
    def __init__(self) -> None:
        self.engine = object()
        self.graph = None
        self.idle = False

    async def cancel_graph_runs(self) -> None:
        pass

    async def wait_idle(self) -> None:
        self.idle = True


def test_lifespan_marks_interrupted_and_drains(monkeypatch: Any) -> None:
    seen: list[object] = []

    def mark(engine: object) -> int:
        seen.append(engine)
        return 0

    monkeypatch.setattr("api.main.mark_interrupted", mark)
    stub = StubService()
    app = create_app(SETTINGS, run_service=stub)  # type: ignore[arg-type]
    with TestClient(app):
        pass
    assert seen == [stub.engine]
    assert stub.idle


def test_lifespan_survives_an_unreachable_database(monkeypatch: Any, capsys: Any) -> None:
    def fail(engine: object) -> int:
        raise RuntimeError("relation runs does not exist")

    monkeypatch.setattr("api.main.mark_interrupted", fail)
    stub = StubService()
    with TestClient(create_app(SETTINGS, run_service=stub)) as client:  # type: ignore[arg-type]
        assert client.get("/healthz").status_code == 200
    assert "could not mark interrupted runs" in capsys.readouterr().out
    assert stub.idle


async def test_build_guards_without_connecting() -> None:
    guards, resources = build_guards(SETTINGS, engine=object())  # type: ignore[arg-type]
    assert guards.redis is resources[0]
    assert len(resources) == 2
    for resource in resources:
        await resource.aclose()


async def test_build_run_service_takes_the_engine_settings() -> None:
    settings = SETTINGS.model_copy(update={"agent_engine": "graph", "agent_max_concurrent_runs": 2})
    service, resources = build_run_service(settings)
    assert service.agent_engine == "graph"
    assert service.graph is None  # attached by graph_runtime in the lifespan
    for resource in resources:
        close = getattr(resource, "aclose", None)
        if close is not None:
            await close()
        else:
            resource.dispose()


async def test_graph_deps_from_settings() -> None:
    settings = SETTINGS.model_copy(
        update={"agent_deep_max_model_calls": 15, "agent_plan_max_steps": 6}
    )
    service, resources = build_run_service(settings)
    deps = graph_deps(service, settings)
    assert deps.budgets["simple"].max_model_calls == 3
    assert deps.budgets["deep"].max_model_calls == 15
    assert deps.plan_max_steps == 6
    assert deps.emit == service.emit_event
    assert deps.sandbox is None
    marker: Any = object()
    assert graph_deps(service, settings, marker).sandbox is marker
    for resource in resources:
        close = getattr(resource, "aclose", None)
        if close is not None:
            await close()
        else:
            resource.dispose()


async def test_graph_runtime_attaches_and_detaches(monkeypatch: Any) -> None:
    opened: list[str] = []

    @asynccontextmanager
    async def fake_checkpointer(url: str) -> AsyncIterator[InMemorySaver]:
        opened.append(url)
        yield InMemorySaver()

    monkeypatch.setattr("api.runtime.open_checkpointer", fake_checkpointer)
    service, resources = build_run_service(SETTINGS)
    async with graph_runtime(service, SETTINGS):
        assert service.graph is not None
    assert service.graph is None
    assert opened == ["postgresql://u:p@localhost:5432/db"]
    for resource in resources:
        close = getattr(resource, "aclose", None)
        if close is not None:
            await close()
        else:
            resource.dispose()


def test_lifespan_builds_the_runtime_and_the_graph(monkeypatch: Any) -> None:
    stub = StubService()
    entered: list[str] = []

    def build(settings: Settings) -> tuple[Any, list[Any]]:
        return stub, []

    @asynccontextmanager
    async def runtime(service: Any, settings: Settings) -> AsyncIterator[None]:
        entered.append("open")
        yield
        entered.append("close")

    monkeypatch.setattr("api.runtime.build_run_service", build)
    monkeypatch.setattr("api.runtime.graph_runtime", runtime)
    monkeypatch.setattr("api.main.mark_interrupted", lambda engine: 0)
    app = create_app(SETTINGS, guards=object())  # type: ignore[arg-type]
    with TestClient(app):
        assert entered == ["open"]
    assert entered == ["open", "close"]
    assert stub.idle
