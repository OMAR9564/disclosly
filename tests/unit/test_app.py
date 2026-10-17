"""Tests for api.main.create_app (wiring of settings, errors, logging and CORS)."""

import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from api.config import Settings, get_settings
from api.main import create_app

ALLOWED = "https://disclosly.com"
DISALLOWED = "https://evil.example"


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    yield
    root.handlers = saved_handlers
    root.setLevel(saved_level)
    logging.getLogger("uvicorn.access").disabled = False


def _settings(app_env: str = "test") -> Settings:
    return Settings(
        app_env=app_env,
        app_version="abc1234",
        database_url="postgresql://u:p@localhost:5432/db",
        redis_url="redis://localhost:6379/0",
        cors_allowed_origins=[ALLOWED],
    )


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(_settings()), raise_server_exceptions=False)


def test_allowed_origin_gets_cors_header(client: TestClient) -> None:
    response = client.get("/nope", headers={"Origin": ALLOWED})

    assert response.headers["access-control-allow-origin"] == ALLOWED
    assert "X-Request-ID" in response.headers["access-control-expose-headers"]


def test_disallowed_origin_gets_no_cors_header(client: TestClient) -> None:
    response = client.get("/nope", headers={"Origin": DISALLOWED})

    assert "access-control-allow-origin" not in response.headers


def test_preflight_from_allowed_origin(client: TestClient) -> None:
    response = client.options(
        "/healthz",
        headers={"Origin": ALLOWED, "Access-Control-Request-Method": "GET"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED
    assert "X-Request-ID" in response.headers


def test_preflight_from_disallowed_origin_is_rejected(client: TestClient) -> None:
    response = client.options(
        "/healthz",
        headers={"Origin": DISALLOWED, "Access-Control-Request-Method": "GET"},
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_unknown_path_returns_not_found_with_request_id(client: TestClient) -> None:
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]


def test_wrong_method_returns_method_not_allowed(client: TestClient) -> None:
    app = create_app(_settings())

    @app.get("/only-get")
    def only_get() -> dict[str, str]:
        return {}

    response = TestClient(app).post("/only-get")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
    assert "X-Request-ID" in response.headers


def test_app_exposes_settings(client: TestClient) -> None:
    assert client.app.state.settings.app_version == "abc1234"  # type: ignore[attr-defined]


@pytest.mark.parametrize(("app_env", "docs_status"), [("development", 200), ("production", 404)])
def test_docs_only_in_development(app_env: str, docs_status: int) -> None:
    response = TestClient(create_app(_settings(app_env))).get("/docs")

    assert response.status_code == docs_status


def test_configures_json_logging() -> None:
    create_app(_settings())

    [handler] = logging.getLogger().handlers
    assert type(handler.formatter).__name__ == "JsonFormatter"


def test_factory_without_arguments_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_VERSION", "fromenv1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", ALLOWED)
    get_settings.cache_clear()
    try:
        app = create_app()
    finally:
        get_settings.cache_clear()

    assert app.state.settings.app_version == "fromenv1"
