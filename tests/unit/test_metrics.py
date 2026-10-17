"""Tests for the Prometheus middleware and GET /metrics (contract: getMetrics)."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from api.config import Settings
from api.logging import UNMATCHED_ROUTE
from api.main import create_app


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    yield
    root.handlers = saved_handlers
    root.setLevel(saved_level)
    logging.getLogger("uvicorn.access").disabled = False


def _client(multiproc_dir: str | None = None) -> TestClient:
    settings = Settings(
        app_env="test",
        app_version="abc1234",
        database_url="postgresql://u:p@192.0.2.1:5432/db",
        redis_url="redis://192.0.2.1:6379/0",
        cors_allowed_origins=["https://disclosly.com"],
        prometheus_multiproc_dir=multiproc_dir,
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


def _requests_total(method: str, route: str, status: str) -> float:
    value = REGISTRY.get_sample_value(
        "http_requests_total", {"method": method, "route": route, "status": status}
    )
    return value or 0.0


def _duration_count(method: str, route: str) -> float:
    value = REGISTRY.get_sample_value(
        "http_request_duration_seconds_count", {"method": method, "route": route}
    )
    return value or 0.0


def test_counter_and_histogram_increase_after_a_request() -> None:
    client = _client()
    before_total = _requests_total("GET", "/healthz", "200")
    before_count = _duration_count("GET", "/healthz")

    client.get("/healthz")

    assert _requests_total("GET", "/healthz", "200") == before_total + 1
    assert _duration_count("GET", "/healthz") == before_count + 1


def test_unknown_paths_share_one_route_label() -> None:
    client = _client()
    before = _requests_total("GET", UNMATCHED_ROUTE, "404")

    client.get("/does-not-exist-1")
    client.get("/does-not-exist-2/abc")

    assert _requests_total("GET", UNMATCHED_ROUTE, "404") == before + 2
    assert (
        REGISTRY.get_sample_value(
            "http_requests_total", {"method": "GET", "route": "/does-not-exist-1", "status": "404"}
        )
        is None
    )


def test_metrics_endpoint_serves_text_format_0_0_4() -> None:
    client = _client()
    client.get("/healthz")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "http_requests_total" in response.text
    assert "http_request_duration_seconds_bucket" in response.text


def test_metrics_endpoint_aggregates_multiprocess_dir(tmp_path: Path) -> None:
    response = _client(multiproc_dir=str(tmp_path)).get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
