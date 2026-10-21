"""Tests for api.runtime and the app lifespan (T019 wiring)."""

from typing import Any

from fastapi.testclient import TestClient

from api.config import Settings
from api.main import create_app
from api.runtime import LazySearch, budget_from, build_run_service
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
