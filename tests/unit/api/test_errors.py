"""Tests for the Phase 3 error codes and localized messages (T006)."""

from typing import get_args

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.errors import (
    ERROR_CODES,
    ApiError,
    ErrorCode,
    localized_message,
    message_language,
    register_exception_handlers,
)

PHASE3_CODES: dict[ErrorCode, int] = {
    "unauthorized": 401,
    "beta_closed": 403,
    "bot_check_failed": 403,
    "quota_exhausted": 429,
    "rate_limited": 429,
    "providers_unavailable": 503,
}


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise/{code}")
    def raise_code(code: str) -> None:
        if code == "rate_limited":
            raise ApiError("rate_limited", headers={"Retry-After": "17"})
        if code == "quota_exhausted":
            raise ApiError("quota_exhausted", params={"resets_at": "00:00"})
        raise ApiError(code)  # type: ignore[arg-type]

    @app.get("/item/{item_id}")
    def item(item_id: int) -> None:
        return None

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app(), raise_server_exceptions=False)


@pytest.mark.parametrize(("code", "status"), PHASE3_CODES.items())
def test_codes_render_envelope_with_status(client: TestClient, code: str, status: int) -> None:
    assert ERROR_CODES[code] == status  # type: ignore[index]
    response = client.get(f"/raise/{code}")

    assert response.status_code == status
    body = response.json()["error"]
    assert body["code"] == code
    assert body["message"]
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_rate_limited_sets_retry_after(client: TestClient) -> None:
    response = client.get("/raise/rate_limited")
    assert response.headers["Retry-After"] == "17"


def test_quota_message_includes_reset_time(client: TestClient) -> None:
    response = client.get("/raise/quota_exhausted", headers={"Accept-Language": "en"})
    assert "00:00" in response.json()["error"]["message"]


def test_message_language_follows_accept_language(client: TestClient) -> None:
    english = client.get("/raise/unauthorized", headers={"Accept-Language": "en-US,en;q=0.9"})
    turkish = client.get("/raise/unauthorized", headers={"Accept-Language": "tr-TR"})

    assert english.json()["error"]["message"] == "Please sign in."
    assert turkish.json()["error"]["message"] == "Lütfen giriş yapın."


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, "tr"),
        ("", "tr"),
        ("en", "en"),
        ("tr", "tr"),
        ("de-DE, en;q=0.5", "en"),
        ("en;q=0.3, tr;q=0.8", "tr"),
        ("en;q=0", "tr"),
        ("fr", "tr"),
        ("en;q=abc", "tr"),
    ],
)
def test_message_language(header: str | None, expected: str) -> None:
    assert message_language(header) == expected


@pytest.mark.parametrize("code", get_args(ErrorCode))
@pytest.mark.parametrize("language", ["tr", "en"])
def test_every_code_has_both_languages(code: ErrorCode, language: str) -> None:
    message = localized_message(code, language)  # type: ignore[arg-type]
    assert message
    assert "{" not in message


def test_validation_failure_logs_location_but_not_input(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="api.errors")
    response = client.get("/item/secret-value")
    assert response.status_code == 422
    record = next(r for r in caplog.records if r.getMessage() == "request validation failed")
    assert record.errors == [{"loc": ["path", "item_id"], "type": "int_parsing"}]  # type: ignore[attr-defined]
    assert "secret-value" not in str(record.__dict__)
