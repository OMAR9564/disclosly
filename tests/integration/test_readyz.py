"""GET /readyz against real Postgres and Redis (CI service containers or `make up`)."""

import logging
import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from api.config import Settings
from api.main import create_app

pytestmark = pytest.mark.integration

LOCAL_DATABASE_URL = "postgresql://disclosly:CHANGE_ME@localhost:5432/disclosly"
LOCAL_REDIS_URL = "redis://localhost:6379/0"


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    yield
    root.handlers = saved_handlers
    root.setLevel(saved_level)
    logging.getLogger("uvicorn.access").disabled = False


def _client(database_url: str, redis_url: str) -> TestClient:
    settings = Settings(
        app_env="test",
        database_url=database_url,
        redis_url=redis_url,
        cors_allowed_origins=["https://disclosly.com"],
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


def test_ready_against_real_services() -> None:
    client = _client(
        os.environ.get("DATABASE_URL", LOCAL_DATABASE_URL),
        os.environ.get("REDIS_URL", LOCAL_REDIS_URL),
    )

    response = client.get("/readyz")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert {name: check["status"] for name, check in body["checks"].items()} == {
        "postgres": "ok",
        "redis": "ok",
    }


def test_not_ready_when_redis_port_is_closed() -> None:
    # Port 1 on localhost is closed, so the connection is refused at once.
    client = _client(
        os.environ.get("DATABASE_URL", LOCAL_DATABASE_URL),
        "redis://localhost:1/0",
    )

    response = client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["postgres"]["status"] == "ok"
    assert body["checks"]["redis"] == {
        "status": "error",
        "latency_ms": body["checks"]["redis"]["latency_ms"],
        "error": "connection_refused",
    }
