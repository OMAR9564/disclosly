"""Auth endpoints (contracts/http-api.md, "Auth").

Sign-in failures on the callback redirect to `{WEB_ORIGIN}/signin?error=<reason>`; a verified
email that is not on the allowlist redirects to `/beta-closed` and is not stored.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine, select, update
from sqlalchemy.dialects.postgresql import insert

from api import schema
from api.auth import oauth
from api.auth.allowlist import email_hash, mask_email
from api.auth.session import (
    REFRESH_COOKIE,
    RefreshRedis,
    clear_auth_cookies,
    consume_refresh,
    issue_refresh,
    issue_session,
    set_auth_cookies,
)
from api.config import Settings
from api.errors import ApiError
from api.guards import get_guards, ip_rate_limit
from api.guards.ratelimit import client_ip
from api.guards.turnstile import verify_turnstile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/auth", tags=["auth"], dependencies=[Depends(ip_rate_limit)])


class StartRequest(BaseModel):
    turnstile_token: str = Field(max_length=2048)
    return_to: str | None = Field(default=None, max_length=oauth.MAX_RETURN_TO)


class StartResponse(BaseModel):
    authorize_url: str


def _settings(request: Request) -> Settings:
    settings = request.app.state.settings
    assert isinstance(settings, Settings)  # noqa: S101
    return settings


@router.post("/{provider}/start")
async def start_sign_in(
    provider: oauth.ProviderName, body: StartRequest, request: Request
) -> StartResponse:
    settings = _settings(request)
    guards = get_guards(request)
    client = oauth.credentials(provider, settings)
    if client is None:
        raise ApiError("not_found")
    return_to = oauth.safe_return_to(body.return_to, settings)
    if return_to is None:
        raise ApiError("validation_error")
    await verify_turnstile(
        guards.http,
        settings.turnstile_secret_key,
        body.turnstile_token,
        client_ip(request, settings.trusted_proxies),
    )
    url = await oauth.start(guards.redis, provider, client[0], settings, return_to)
    return StartResponse(authorize_url=url)


def _upsert_user(engine: Engine, identity: oauth.Identity) -> uuid.UUID:
    """Returns (user id, email); keyed by provider subject first, then by email."""
    users = schema.users
    now = datetime.now(UTC)
    with engine.begin() as conn:
        row = conn.execute(
            update(users)
            .where(
                users.c.auth_provider == identity.provider,
                users.c.provider_subject == identity.subject,
            )
            .values(email=identity.email, display_name=identity.display_name, last_seen_at=now)
            .returning(users.c.id)
        ).first()
        if row is None:
            statement = insert(users).values(
                email=identity.email,
                display_name=identity.display_name,
                auth_provider=identity.provider,
                provider_subject=identity.subject,
            )
            row = conn.execute(
                statement.on_conflict_do_update(
                    index_elements=["email"],
                    set_={"display_name": identity.display_name, "last_seen_at": now},
                ).returning(users.c.id)
            ).one()
    user_id = row[0]
    assert isinstance(user_id, uuid.UUID)  # noqa: S101
    return user_id


def _fail(settings: Settings, reason: str) -> RedirectResponse:
    return RedirectResponse(f"{settings.web_origin}/signin?error={reason}", status_code=302)


@router.get("/{provider}/callback")
async def finish_sign_in(
    provider: oauth.ProviderName,
    request: Request,
    code: str = "",
    state: str = "",
) -> RedirectResponse:
    settings = _settings(request)
    guards = get_guards(request)
    client = oauth.credentials(provider, settings)
    if client is None:
        raise ApiError("not_found")
    try:
        pending = await oauth.take_state(guards.redis, provider, state)
        identity = await oauth.exchange(guards.http, pending, code, client, settings)
    except oauth.OAuthError as exc:
        logger.info("sign-in failed", extra={"provider": provider, "reason": exc.reason})
        return _fail(settings, exc.reason)
    if not await guards.allowlist.allows(identity.email):
        logger.info("sign-in not allowlisted", extra={"email": mask_email(identity.email)})
        return RedirectResponse(f"{settings.web_origin}/beta-closed", status_code=302)

    user_id = await asyncio.to_thread(_upsert_user, guards.engine, identity)
    response = RedirectResponse(pending.return_to, status_code=302)
    await _sign_in(response, settings, guards.redis, user_id, identity.email)
    return response


async def _sign_in(
    response: Response, settings: Settings, redis: RefreshRedis, user_id: uuid.UUID, email: str
) -> None:
    refresh = await issue_refresh(redis, user_id)
    session = issue_session(user_id, settings, email_hash=email_hash(email))
    set_auth_cookies(response, settings, session, refresh)


def _email_of(engine: Engine, user_id: uuid.UUID) -> str | None:
    users = schema.users
    with engine.connect() as conn:
        email = conn.execute(select(users.c.email).where(users.c.id == user_id)).scalar()
    return email if isinstance(email, str) else None


@router.post("/refresh", status_code=204)
async def refresh(
    request: Request, token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None
) -> Response:
    settings = _settings(request)
    guards = get_guards(request)
    user_id = await consume_refresh(guards.redis, token or "")
    email = await asyncio.to_thread(_email_of, guards.engine, user_id) if user_id else None
    if user_id is None or email is None:
        raise ApiError("unauthorized")
    if not await guards.allowlist.allows(email):
        raise ApiError("beta_closed")
    response = Response(status_code=204)
    await _sign_in(response, settings, guards.redis, user_id, email)
    return response


@router.post("/logout", status_code=204)
async def logout(
    request: Request, token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None
) -> Response:
    settings = _settings(request)
    if token:
        await consume_refresh(get_guards(request).redis, token)
    response = Response(status_code=204)
    clear_auth_cookies(response, settings)
    return response
