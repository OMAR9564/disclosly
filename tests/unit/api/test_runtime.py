"""Tests for api.runtime and the app lifespan (T019 wiring)."""

import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.config import Settings
from api.main import create_app
from api.runtime import LazySearch, budget_from, build_guards, build_run_service
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
    assert len(resources) == 3
    for resource in resources:
        close = getattr(resource, "aclose", None)
        if close is not None:
            await close()
        else:
            resource.dispose()


class StubService:
    def __init__(self) -> None:
        self.engine = object()
        self.idle = False

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


def test_lazy_search_is_constructible() -> None:
    assert LazySearch() is not None


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
