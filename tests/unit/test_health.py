"""Tests for GET /healthz (contract: HealthStatus in specs/001-walking-skeleton/contracts)."""

import logging
import socket
from collections.abc import Iterator
from typing import NoReturn

import pytest
from fastapi.testclient import TestClient

from api.config import Settings
from api.main import create_app


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    yield
    root.handlers = saved_handlers
    root.setLevel(saved_level)
    logging.getLogger("uvicorn.access").disabled = False


def _client(app_version: str = "abc1234") -> TestClient:
    settings = Settings(
        app_env="test",
        app_version=app_version,
        # Unroutable on purpose: liveness must never touch dependencies.
        database_url="postgresql://u:p@192.0.2.1:5432/db",
        redis_url="redis://192.0.2.1:6379/0",
        cors_allowed_origins=["https://disclosly.com"],
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


def test_healthz_returns_exact_body() -> None:
    response = _client().get("/healthz")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"status": "ok", "version": "abc1234", "service": "disclosly-api"}


def test_healthz_reports_dev_version_by_default() -> None:
    response = _client(app_version="dev").get("/healthz")

    assert response.json()["version"] == "dev"


def test_healthz_echoes_request_id() -> None:
    response = _client().get("/healthz", headers={"X-Request-ID": "probe-1"})

    assert response.headers["X-Request-ID"] == "probe-1"


def test_healthz_makes_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[object] = []

    def _blocked(*args: object, **kwargs: object) -> NoReturn:
        attempts.append(args)
        raise AssertionError("liveness must not open network connections")

    client = _client()
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert attempts == []


def test_healthz_rejects_other_methods() -> None:
    response = _client().post("/healthz")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
