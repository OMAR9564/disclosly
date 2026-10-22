"""Tests for api.errors (ErrorEnvelope, data-model.md error code catalogue)."""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import ASGIApp, Receive, Scope, Send

from api.errors import ERROR_CODES, ApiError, localized_message, register_exception_handlers

LEAKY_TEXT = "boom: password=hunter2 at /srv/app/secret.py"


class _FixedRequestId:
    """Stands in for the logging middleware: puts a request ID on the scope state."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope.setdefault("state", {})["request_id"] = "fixed-id-123"
        await self.app(scope, receive, send)


def _app(with_request_id: bool = False) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/items/{item_id}")
    def read_item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError(LEAKY_TEXT)

    @app.get("/plan")
    def plan() -> None:
        reasons = [{"step": "s2", "field": "goal", "reason": "asks for investment advice"}]
        raise ApiError("plan_invalid", details={"reasons": reasons})

    @app.get("/decided")
    def decided() -> None:
        raise ApiError("already_decided")

    @app.get("/teapot")
    def teapot() -> None:
        from fastapi import HTTPException

        raise HTTPException(status_code=422)

    if with_request_id:
        app.add_middleware(_FixedRequestId)
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app(), raise_server_exceptions=False)


def _assert_envelope(body: dict[str, object], code: str) -> dict[str, object]:
    assert set(body) == {"error"}
    error = body["error"]
    assert isinstance(error, dict)
    assert set(error) == {"code", "message", "request_id", "details"}
    assert error["code"] == code
    assert isinstance(error["message"], str)
    assert error["message"]
    return error


def test_catalogue_matches_data_model() -> None:
    assert ERROR_CODES == {
        "not_found": 404,
        "method_not_allowed": 405,
        "validation_error": 422,
        "dependency_unavailable": 503,
        "internal_error": 500,
        "unauthorized": 401,
        "beta_closed": 403,
        "bot_check_failed": 403,
        "quota_exhausted": 429,
        "rate_limited": 429,
        "providers_unavailable": 503,
        "already_decided": 409,
        "not_awaiting_decision": 409,
        "plan_expired": 410,
        "plan_invalid": 422,
        "watchlist_full": 422,
        "company_not_covered": 422,
        "sandbox_unavailable": 503,
        "interrupted_limit": 500,
    }


def test_unknown_route_returns_not_found(client: TestClient) -> None:
    response = client.get("/nope")

    assert response.status_code == 404
    error = _assert_envelope(response.json(), "not_found")
    assert error["details"] is None
    assert error["request_id"] == response.headers["X-Request-ID"]


def test_wrong_method_returns_method_not_allowed(client: TestClient) -> None:
    response = client.post("/items/1")

    assert response.status_code == 405
    error = _assert_envelope(response.json(), "method_not_allowed")
    assert error["details"] is None
    assert response.headers["Allow"] == "GET"


def test_validation_error_has_details(client: TestClient) -> None:
    response = client.get("/items/not-a-number")

    assert response.status_code == 422
    error = _assert_envelope(response.json(), "validation_error")
    details = error["details"]
    assert isinstance(details, list)
    assert details
    for item in details:
        assert set(item) == {"loc", "msg"}
    assert details[0]["loc"] == ["path", "item_id"]


def test_validation_error_does_not_echo_input(client: TestClient) -> None:
    response = client.get("/items/user@example.com")

    assert response.status_code == 422
    assert "user@example.com" not in response.text


def test_unhandled_exception_returns_generic_internal_error(client: TestClient) -> None:
    response = client.get("/boom")

    assert response.status_code == 500
    error = _assert_envelope(response.json(), "internal_error")
    assert error["details"] is None
    assert error["request_id"] == response.headers["X-Request-ID"]
    assert "boom" not in response.text
    assert "hunter2" not in response.text
    assert "RuntimeError" not in response.text
    assert "Traceback" not in response.text
    assert "secret.py" not in response.text


def test_request_id_without_middleware_is_uuid4(client: TestClient) -> None:
    response = client.get("/nope")
    request_id = response.json()["error"]["request_id"]

    assert uuid.UUID(request_id).version == 4


@pytest.mark.parametrize("path", ["/nope", "/boom"])
def test_request_id_comes_from_request_state(path: str) -> None:
    client = TestClient(_app(with_request_id=True), raise_server_exceptions=False)
    response = client.get(path)

    assert response.json()["error"]["request_id"] == "fixed-id-123"
    assert response.headers["X-Request-ID"] == "fixed-id-123"


PHASE4_CODES = [
    "already_decided",
    "not_awaiting_decision",
    "plan_expired",
    "plan_invalid",
    "watchlist_full",
    "company_not_covered",
    "sandbox_unavailable",
    "interrupted_limit",
]


@pytest.mark.parametrize("code", PHASE4_CODES)
def test_phase4_codes_have_both_languages(code: str) -> None:
    en = localized_message(code, "en")  # type: ignore[arg-type]
    tr = localized_message(code, "tr")  # type: ignore[arg-type]
    assert en
    assert tr
    assert en != tr


def test_envelope_carries_details(client: TestClient) -> None:
    response = client.get("/plan", headers={"Accept-Language": "en"})

    assert response.status_code == 422
    error = _assert_envelope(response.json(), "plan_invalid")
    assert error["details"] == {
        "reasons": [{"step": "s2", "field": "goal", "reason": "asks for investment advice"}]
    }
    assert error["message"] == "The edited plan is not valid. Please fix the marked steps."


def test_named_code_status_and_message(client: TestClient) -> None:
    response = client.get("/decided")

    assert response.status_code == 409
    error = _assert_envelope(response.json(), "already_decided")
    assert error["message"] == "Bu plan için zaten bir karar verildi."
    assert error["details"] is None


def test_bare_status_keeps_the_generic_code(client: TestClient) -> None:
    # A bare 422 is still a validation error, not one of the Phase 4 codes sharing the status.
    response = client.get("/teapot")
    assert response.status_code == 422
    _assert_envelope(response.json(), "validation_error")
