"""Tests for GET /readyz with fake dependency probes (contract: ReadinessStatus)."""

import asyncio
import errno
import logging
from collections.abc import Iterator

import psycopg
import pytest
import redis.exceptions
from fastapi.testclient import TestClient

from api import health
from api.config import Settings
from api.health import Probe, classify_error
from api.main import create_app

# Fake credential: the test asserts it never reaches the response body.
SECRET_DSN = "postgresql://disclosly:hunter2@db.internal:5432/disclosly"  # noqa: S105


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    yield
    root.handlers = saved_handlers
    root.setLevel(saved_level)
    logging.getLogger("uvicorn.access").disabled = False


async def _ok() -> None:
    return None


async def _hang() -> None:
    await asyncio.sleep(10)


def _raising(exc: BaseException) -> Probe:
    async def probe() -> None:
        raise exc

    return probe


def _client(postgres: Probe, redis_probe: Probe) -> TestClient:
    settings = Settings(
        app_env="test",
        app_version="abc1234",
        database_url=SECRET_DSN,
        redis_url="redis://192.0.2.1:6379/0",
        cors_allowed_origins=["https://disclosly.com"],
    )
    app = create_app(settings)
    app.state.readiness_probes = {"postgres": postgres, "redis": redis_probe}
    return TestClient(app, raise_server_exceptions=False)


def test_ready_when_both_dependencies_ok() -> None:
    response = _client(_ok, _ok).get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["version"] == "abc1234"
    assert set(body) == {"status", "version", "checks"}
    assert set(body["checks"]) == {"postgres", "redis"}
    for check in body["checks"].values():
        assert set(check) == {"status", "latency_ms", "error"}
        assert check["status"] == "ok"
        assert check["error"] is None
        assert check["latency_ms"] >= 0


def test_not_ready_when_one_dependency_fails() -> None:
    response = _client(_ok, _raising(ConnectionRefusedError())).get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["postgres"] == {
        "status": "ok",
        "latency_ms": body["checks"]["postgres"]["latency_ms"],
        "error": None,
    }
    assert body["checks"]["redis"]["status"] == "error"
    assert body["checks"]["redis"]["error"] == "connection_refused"


def test_slow_dependency_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "PROBE_TIMEOUT_S", 0.05)

    response = _client(_hang, _ok).get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["postgres"]["error"] == "timeout"


def test_checks_run_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "PROBE_TIMEOUT_S", 0.3)

    response = _client(_hang, _hang).get("/readyz")

    latencies = [c["latency_ms"] for c in response.json()["checks"].values()]
    assert response.elapsed.total_seconds() < 0.55
    assert all(ms < 550 for ms in latencies)


def test_error_body_never_leaks_connection_details() -> None:
    leak = psycopg.OperationalError(f"connection to {SECRET_DSN} failed: boom")

    response = _client(_raising(leak), _ok).get("/readyz")

    assert response.status_code == 503
    assert "hunter2" not in response.text
    assert "db.internal" not in response.text
    assert "Traceback" not in response.text
    assert response.json()["checks"]["postgres"]["error"] == "unknown"


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (TimeoutError(), "timeout"),
        (ConnectionRefusedError(), "connection_refused"),
        (
            psycopg.OperationalError(
                'connection failed: connection to server at "127.0.0.1", port 5432 failed: '
                "Connection refused"
            ),
            "connection_refused",
        ),
        (
            redis.exceptions.ConnectionError(
                "Error 111 connecting to localhost:6379. Connection refused."
            ),
            "connection_refused",
        ),
        (redis.exceptions.TimeoutError("Timeout connecting to server"), "timeout"),
        (psycopg.errors.InvalidPassword("password authentication failed"), "auth_failed"),
        (
            psycopg.OperationalError('password authentication failed for user "disclosly"'),
            "auth_failed",
        ),
        (redis.exceptions.AuthenticationError("invalid password"), "auth_failed"),
        (redis.exceptions.ResponseError("NOAUTH Authentication required."), "auth_failed"),
        (redis.exceptions.ResponseError("WRONGPASS invalid username-password pair"), "auth_failed"),
        (OSError(errno.ECONNREFUSED, "Connect call failed"), "connection_refused"),
        (ValueError("weird"), "unknown"),
    ],
)
def test_classify_error(exc: BaseException, expected: str) -> None:
    assert classify_error(exc) == expected


def test_classify_error_follows_cause_chain() -> None:
    # redis-py 8 wraps the socket error: ConnectionError("network:ConnectionError") from OSError.
    root = OSError("Multiple exceptions: [Errno 111] Connect call failed ('127.0.0.1', 1)")
    wrapped = redis.exceptions.ConnectionError("network:ConnectionError")
    wrapped.__cause__ = root

    assert classify_error(wrapped) == "connection_refused"


def test_classify_error_survives_cyclic_chain() -> None:
    first, second = ValueError("a"), ValueError("b")
    first.__context__, second.__context__ = second, first

    assert classify_error(first) == "unknown"
