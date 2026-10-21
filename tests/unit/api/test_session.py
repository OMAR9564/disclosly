"""Tests for api.auth.session (T010)."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth.session import (
    SESSION_COOKIE,
    CurrentUser,
    issue_session,
    mint_test_session,
    verify_session,
)
from api.config import Settings
from api.errors import ApiError, register_exception_handlers

SECRET = "jwt-secret-value-that-is-long-enough-123"  # noqa: S105
USER_ID = uuid.UUID("11111111-2222-4333-8444-555555555555")


def _settings(app_env: str = "test", jwt_secret: str | None = SECRET) -> Settings:
    return Settings(
        app_env=app_env,
        database_url="postgresql://u:p@localhost:5432/db",
        redis_url="redis://localhost:6379/0",
        cors_allowed_origins=["https://disclosly.com"],
        jwt_secret=jwt_secret,
    )


def _client(settings: Settings) -> TestClient:
    app = FastAPI()
    app.state.settings = settings
    register_exception_handlers(app)

    @app.get("/whoami")
    async def whoami(user: CurrentUser) -> dict[str, str]:
        return {"id": str(user.id)}

    return TestClient(app)


def test_round_trip() -> None:
    settings = _settings()
    assert verify_session(issue_session(USER_ID, settings), settings).id == USER_ID


def test_claims_and_lifetime() -> None:
    now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    token = issue_session(USER_ID, _settings(), now=now)
    claims = jwt.decode(
        token, SECRET, algorithms=["HS256"], options={"verify_exp": False, "verify_iat": False}
    )

    assert claims["sub"] == str(USER_ID)
    assert claims["exp"] - claims["iat"] == 15 * 60


def test_expired_token_rejected() -> None:
    settings = _settings()
    token = issue_session(USER_ID, settings, now=datetime.now(UTC) - timedelta(minutes=16))
    with pytest.raises(ApiError) as excinfo:
        verify_session(token, settings)
    assert excinfo.value.code == "unauthorized"


def test_tampered_token_rejected() -> None:
    settings = _settings()
    header, payload, signature = issue_session(USER_ID, settings).split(".")
    flipped = signature[:-2] + ("AA" if signature[-2:] != "AA" else "BB")
    with pytest.raises(ApiError):
        verify_session(f"{header}.{payload}.{flipped}", settings)


def test_other_secret_rejected() -> None:
    token = issue_session(USER_ID, _settings(jwt_secret="another-secret-that-is-long-enough-xx"))  # noqa: S106
    with pytest.raises(ApiError):
        verify_session(token, _settings())


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": "not-a-uuid"},
        {"iat": 0},
    ],
)
def test_bad_claims_rejected(claims: dict[str, object]) -> None:
    now = datetime.now(UTC)
    payload = {"sub": str(USER_ID), "iat": now, "exp": now + timedelta(minutes=5)}
    payload.update(claims)
    if claims.get("iat") == 0:
        del payload["sub"]
    token = jwt.encode(payload, SECRET, algorithm="HS256")
    with pytest.raises(ApiError):
        verify_session(token, _settings())


def test_none_algorithm_rejected() -> None:
    token = jwt.encode({"sub": str(USER_ID)}, key=None, algorithm="none")  # type: ignore[arg-type]
    with pytest.raises(ApiError):
        verify_session(token, _settings())


def test_dev_secret_used_when_unset_in_test() -> None:
    settings = _settings(jwt_secret=None)
    assert verify_session(issue_session(USER_ID, settings), settings).id == USER_ID


def test_dependency_accepts_valid_cookie() -> None:
    settings = _settings()
    client = _client(settings)
    client.cookies.update(mint_test_session(USER_ID, settings))

    response = client.get("/whoami")

    assert response.status_code == 200
    assert response.json() == {"id": str(USER_ID)}


def test_dependency_missing_cookie_is_unauthorized() -> None:
    response = _client(_settings()).get("/whoami")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_dependency_garbage_cookie_is_unauthorized() -> None:
    client = _client(_settings())
    client.cookies.set(SESSION_COOKIE, "garbage")
    assert client.get("/whoami").status_code == 401


def test_mint_refused_in_production() -> None:
    with pytest.raises(RuntimeError):
        mint_test_session(USER_ID, _settings(app_env="production"))
