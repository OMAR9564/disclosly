"""Short-lived session JWTs in the `session` cookie (research §R4).

HS256, 15 minutes, claims `sub` (user id), `iat`, `exp`. Refresh tokens and OAuth come in
US3 (T032); the allowlist check is added to `current_user` in T033.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Cookie, Depends, Request

from api.config import Settings
from api.errors import ApiError

SESSION_COOKIE = "session"
SESSION_TTL = timedelta(minutes=15)
ALGORITHM = "HS256"

# Used only when APP_ENV is development or test and JWT_SECRET is unset (api.config enforces a
# real secret everywhere else).
_DEV_SECRET = "disclosly-development-only-secret-not-for-production"  # noqa: S105


@dataclass(frozen=True)
class SessionUser:
    id: uuid.UUID


def _secret(settings: Settings) -> str:
    if settings.jwt_secret is not None:
        return settings.jwt_secret.get_secret_value()
    if settings.app_env in ("development", "test"):
        return _DEV_SECRET
    raise RuntimeError("JWT_SECRET is not configured")  # pragma: no cover - config prevents it


def issue_session(user_id: uuid.UUID, settings: Settings, now: datetime | None = None) -> str:
    issued = now or datetime.now(UTC)
    claims = {"sub": str(user_id), "iat": issued, "exp": issued + SESSION_TTL}
    return jwt.encode(claims, _secret(settings), algorithm=ALGORITHM)


def verify_session(token: str, settings: Settings) -> SessionUser:
    """Raises `ApiError("unauthorized")` for any invalid, expired or tampered token."""
    try:
        claims = jwt.decode(
            token,
            _secret(settings),
            algorithms=[ALGORITHM],
            options={"require": ["sub", "iat", "exp"]},
        )
        return SessionUser(id=uuid.UUID(str(claims["sub"])))
    except (jwt.PyJWTError, ValueError):
        raise ApiError("unauthorized") from None


def _settings(request: Request) -> Settings:
    settings = request.app.state.settings
    assert isinstance(settings, Settings)  # noqa: S101  (set by create_app)
    return settings


async def current_user(
    request: Request, session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None
) -> SessionUser:
    """FastAPI dependency: the signed-in user, or `unauthorized`."""
    if not session:
        raise ApiError("unauthorized")
    return verify_session(session, _settings(request))


CurrentUser = Annotated[SessionUser, Depends(current_user)]


def mint_test_session(user_id: uuid.UUID, settings: Settings) -> dict[str, str]:
    """Test helper: cookies for a signed-in user. Refuses to run in production."""
    if settings.app_env == "production":
        raise RuntimeError("test sessions are not available in production")
    return {SESSION_COOKIE: issue_session(user_id, settings)}
