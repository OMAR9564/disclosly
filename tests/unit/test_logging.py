"""Tests for api.logging (RequestLogRecord and redaction rules in data-model.md)."""

import io
import json
import logging
import uuid
from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.errors import register_exception_handlers
from api.logging import (
    REDACTED,
    UNMATCHED_ROUTE,
    JsonFormatter,
    RedactionFilter,
    RequestContextMiddleware,
    configure_logging,
)


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    """Capture JSON log lines written by a handler using the real formatter and filter."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    yield buffer
    root.removeHandler(handler)
    root.setLevel(previous_level)


def _lines(buffer: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line]


def _request_lines(buffer: io.StringIO) -> list[dict[str, object]]:
    return [line for line in _lines(buffer) if line.get("msg") == "request"]


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(RequestContextMiddleware, version="abc1234")

    @app.get("/items/{item_id}")
    def read_item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("boom")

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app(), raise_server_exceptions=False)


# --- Formatter -----------------------------------------------------------------------------


def test_formatter_writes_one_json_object(stream: io.StringIO) -> None:
    logging.getLogger("test").info("hello %s", "world", extra={"answer": 42})

    [line] = _lines(stream)
    assert line["msg"] == "hello world"
    assert line["level"] == "info"
    assert line["answer"] == 42
    ts = datetime.fromisoformat(str(line["ts"]))
    assert ts.utcoffset() is not None
    assert ts.utcoffset().total_seconds() == 0  # type: ignore[union-attr]


def test_formatter_drops_uvicorn_color_message(stream: io.StringIO) -> None:
    logging.getLogger("test").info("started", extra={"color_message": "\x1b[36mstarted\x1b[0m"})

    [line] = _lines(stream)
    assert "color_message" not in line


def test_formatter_includes_exception_server_side(stream: io.StringIO) -> None:
    try:
        raise ValueError("bad")
    except ValueError:
        logging.getLogger("test").exception("failed")

    [line] = _lines(stream)
    assert line["level"] == "error"
    assert "ValueError: bad" in str(line["exc_info"])


# --- Redaction -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "api_key",
        "KEY",
        "access_token",
        "client_secret",
        "db_password",
        "Authorization",
        "authorization",
    ],
)
def test_sensitive_field_names_are_redacted(stream: io.StringIO, field: str) -> None:
    logging.getLogger("test").info("x", extra={field: "very-sensitive"})

    [line] = _lines(stream)
    assert line[field] == REDACTED
    assert "very-sensitive" not in stream.getvalue()


def test_email_values_are_redacted(stream: io.StringIO) -> None:
    logging.getLogger("test").info(
        "signup from %s", "jane.doe@example.com", extra={"user": "john@example.org"}
    )

    [line] = _lines(stream)
    assert "@example" not in stream.getvalue()
    assert line["msg"] == f"signup from {REDACTED}"
    assert line["user"] == REDACTED


def test_sensitive_query_values_are_redacted(stream: io.StringIO) -> None:
    logging.getLogger("httpx").info(
        'HTTP Request: GET https://api/v1/auth/google/callback?code=abc123&state=s-9&page=2 "302"'
    )
    logging.getLogger("t").info("GET /x?api_key=k1&access_token=t1")
    first, second = _lines(stream)
    assert first["msg"] == (
        "HTTP Request: GET https://api/v1/auth/google/callback"
        f'?code={REDACTED}&state={REDACTED}&page=2 "302"'
    )
    assert second["msg"] == f"GET /x?api_key={REDACTED}&access_token={REDACTED}"


def test_nested_values_are_redacted(stream: io.StringIO) -> None:
    logging.getLogger("test").info(
        "x", extra={"context": {"token": "t-1", "items": ["a@b.co", "plain"], "safe": 1}}
    )

    [line] = _lines(stream)
    assert line["context"] == {"token": REDACTED, "items": [REDACTED, "plain"], "safe": 1}


def test_non_sensitive_fields_are_kept(stream: io.StringIO) -> None:
    logging.getLogger("test").info("x", extra={"route": "/items/{item_id}", "status": 200})

    [line] = _lines(stream)
    assert line["route"] == "/items/{item_id}"
    assert line["status"] == 200


# --- Request middleware --------------------------------------------------------------------


def test_request_line_has_required_fields(client: TestClient, stream: io.StringIO) -> None:
    response = client.get("/items/7")

    assert response.status_code == 200
    [line] = _request_lines(stream)
    assert set(line) >= {
        "ts",
        "level",
        "msg",
        "request_id",
        "method",
        "route",
        "status",
        "latency_ms",
        "version",
    }
    assert line["level"] == "info"
    assert line["method"] == "GET"
    assert line["route"] == "/items/{item_id}"
    assert line["status"] == 200
    assert isinstance(line["latency_ms"], float)
    assert line["latency_ms"] >= 0
    assert line["version"] == "abc1234"
    assert line["request_id"] == response.headers["X-Request-ID"]


def test_valid_incoming_request_id_is_echoed(client: TestClient, stream: io.StringIO) -> None:
    response = client.get("/items/7", headers={"X-Request-ID": "Trace-ABC-123"})

    assert response.headers["X-Request-ID"] == "Trace-ABC-123"
    [line] = _request_lines(stream)
    assert line["request_id"] == "Trace-ABC-123"


@pytest.mark.parametrize(
    "incoming",
    [b"x" * 65, b"has space", b"semi;colon", b"under_score", "ünicode".encode(), b""],
)
def test_invalid_incoming_request_id_is_replaced(client: TestClient, incoming: bytes) -> None:
    response = client.get("/items/7", headers={b"X-Request-ID": incoming})

    request_id = response.headers["X-Request-ID"]
    assert request_id.encode() != incoming
    assert uuid.UUID(request_id).version == 4


def test_max_length_request_id_is_accepted(client: TestClient) -> None:
    incoming = "a" * 64
    response = client.get("/items/7", headers={"X-Request-ID": incoming})

    assert response.headers["X-Request-ID"] == incoming


def test_error_envelope_uses_same_request_id(client: TestClient) -> None:
    response = client.get("/nope", headers={"X-Request-ID": "req-1"})

    assert response.headers["X-Request-ID"] == "req-1"
    assert response.json()["error"]["request_id"] == "req-1"


def test_4xx_logs_at_info_with_unmatched_route(client: TestClient, stream: io.StringIO) -> None:
    client.get("/nope/deeper")

    [line] = _request_lines(stream)
    assert line["level"] == "info"
    assert line["status"] == 404
    assert line["route"] == UNMATCHED_ROUTE


def test_405_logs_route_template(client: TestClient, stream: io.StringIO) -> None:
    client.post("/items/7")

    [line] = _request_lines(stream)
    assert line["status"] == 405
    assert line["route"] == "/items/{item_id}"


def test_5xx_logs_at_error(client: TestClient, stream: io.StringIO) -> None:
    response = client.get("/boom", headers={"X-Request-ID": "req-500"})

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "req-500"
    [line] = _request_lines(stream)
    assert line["level"] == "error"
    assert line["status"] == 500
    assert line["route"] == "/boom"
    assert line["request_id"] == "req-500"


# --- configure_logging ---------------------------------------------------------------------


def test_configure_logging_installs_json_handler() -> None:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        configure_logging("WARNING")

        assert root.level == logging.WARNING
        [handler] = root.handlers
        assert isinstance(handler.formatter, JsonFormatter)
        assert any(isinstance(f, RedactionFilter) for f in handler.filters)
        assert logging.getLogger("uvicorn.access").disabled
        assert logging.getLogger("uvicorn.error").handlers == []
        assert logging.getLogger("uvicorn.error").propagate
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)
        logging.getLogger("uvicorn.access").disabled = False
