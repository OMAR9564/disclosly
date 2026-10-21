"""Session JWTs and refresh tokens (research §R4).

The `session` cookie holds an HS256 JWT valid for 15 minutes with claims `sub` (user id),
`eh` (SHA-256 of the lower-cased email, for the allowlist check), `iat` and `exp`. The
`refresh` cookie holds a random token id; `refresh:{id}` in Redis maps it to the user for
7 days. Refreshing deletes the old id and issues a new one (rotation), so a stolen refresh
token works once at most.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Protocol

import jwt
from fastapi import Cookie, Depends, Request, Response

from api.config import Settings
from api.errors import ApiError

SESSION_COOKIE = "session"
REFRESH_COOKIE = "refresh"
SESSION_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=7)
REFRESH_COOKIE_PATH = "/v1/auth"
ALGORITHM = "HS256"

# Used only when APP_ENV is development or test and JWT_SECRET is unset (api.config enforces a
# real secret everywhere else).
_DEV_SECRET = "disclosly-development-only-secret-not-for-production"  # noqa: S105


class RefreshRedis(Protocol):
    async def set(self, name: str, value: str, ex: int | None = None) -> Any: ...

    async def getdel(self, name: str) -> Any: ...

    async def delete(self, *names: str) -> Any: ...


@dataclass(frozen=True)
class SessionUser:
    id: uuid.UUID
    email_hash: str


def _secret(settings: Settings) -> str:
    if settings.jwt_secret is not None:
        return settings.jwt_secret.get_secret_value()
    if settings.app_env in ("development", "test"):
        return _DEV_SECRET
    raise RuntimeError("JWT_SECRET is not configured")  # pragma: no cover - config prevents it


def issue_session(
    user_id: uuid.UUID, settings: Settings, now: datetime | None = None, *, email_hash: str
) -> str:
    issued = now or datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "eh": email_hash,
        "iat": issued,
        "exp": issued + SESSION_TTL,
    }
    return jwt.encode(claims, _secret(settings), algorithm=ALGORITHM)


def verify_session(token: str, settings: Settings) -> SessionUser:
    """Raises `ApiError("unauthorized")` for any invalid, expired or tampered token."""
    try:
        claims = jwt.decode(
            token,
            _secret(settings),
            algorithms=[ALGORITHM],
            options={"require": ["sub", "eh", "iat", "exp"]},
        )
        return SessionUser(id=uuid.UUID(str(claims["sub"])), email_hash=str(claims["eh"]))
    except (jwt.PyJWTError, ValueError):
        raise ApiError("unauthorized") from None


def refresh_key(token_id: str) -> str:
    return f"refresh:{token_id}"


async def issue_refresh(redis: RefreshRedis, user_id: uuid.UUID) -> str:
    token_id = secrets.token_urlsafe(32)
    await redis.set(refresh_key(token_id), str(user_id), ex=int(REFRESH_TTL.total_seconds()))
    return token_id


async def consume_refresh(redis: RefreshRedis, token_id: str) -> uuid.UUID | None:
    """Returns the user and revokes the token in one step; `None` if unknown or used."""
    if not token_id or len(token_id) > 128:
        return None
    value = await redis.getdel(refresh_key(token_id))
    if value is None:
        return None
    try:
        return uuid.UUID(value.decode() if isinstance(value, bytes) else str(value))
    except ValueError:
        return None


def set_auth_cookies(
    response: Response, settings: Settings, session_token: str, refresh_token: str
) -> None:
    common: dict[str, Any] = {
        "httponly": True,
        "secure": True,
        "samesite": "lax",
        "domain": settings.session_cookie_domain,
    }
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=int(SESSION_TTL.total_seconds()),
        path="/",
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=int(REFRESH_TTL.total_seconds()),
        path=REFRESH_COOKIE_PATH,
        **common,
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    common: dict[str, Any] = {
        "httponly": True,
        "secure": True,
        "samesite": "lax",
        "domain": settings.session_cookie_domain,
    }
    response.delete_cookie(SESSION_COOKIE, path="/", **common)
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, **common)


def _settings(request: Request) -> Settings:
    settings = request.app.state.settings
    assert isinstance(settings, Settings)  # noqa: S101  (set by create_app)
    return settings


async def current_user(
    request: Request, session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None
) -> SessionUser:
    """FastAPI dependency: the signed-in, allowlisted user, within the per-user rate limit."""
    from api.guards import get_guards

    if not session:
        raise ApiError("unauthorized")
    user = verify_session(session, _settings(request))
    guards = get_guards(request)
    if not await guards.allowlist.allows_hash(user.email_hash):
        raise ApiError("beta_closed")
    await guards.ratelimit.check_user(str(user.id))
    return user


CurrentUser = Annotated[SessionUser, Depends(current_user)]


def mint_test_session(
    user_id: uuid.UUID, settings: Settings, email: str = "user@example.test"
) -> dict[str, str]:
    """Test helper: cookies for a signed-in user. Refuses to run in production."""
    from api.auth.allowlist import email_hash

    if settings.app_env == "production":
        raise RuntimeError("test sessions are not available in production")
    return {SESSION_COOKIE: issue_session(user_id, settings, email_hash=email_hash(email))}
