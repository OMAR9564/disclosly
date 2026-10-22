"""Profile endpoints (contracts/http-api.md, "Me")."""

import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import Engine, select, update

from api import schema
from api.auth.allowlist import mask_email
from api.auth.session import CurrentUser
from api.errors import ApiError
from api.guards import get_guards, ip_rate_limit
from api.guards.quota import Quotas

router = APIRouter(prefix="/v1/me", tags=["me"], dependencies=[Depends(ip_rate_limit)])

Language = Literal["tr", "en"]


class Me(BaseModel):
    display_name: str
    email_masked: str
    language: Language
    quota: Quotas


class MeUpdate(BaseModel):
    language: Language


def _profile(engine: Engine, user_id: uuid.UUID) -> tuple[str, str, Language] | None:
    users = schema.users
    with engine.connect() as conn:
        row = conn.execute(
            select(users.c.display_name, users.c.email, users.c.language).where(
                users.c.id == user_id
            )
        ).first()
    return None if row is None else (row.display_name, row.email, row.language)


def _set_language(engine: Engine, user_id: uuid.UUID, language: Language) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(schema.users).where(schema.users.c.id == user_id).values(language=language)
        )


async def _me(request: Request, user_id: uuid.UUID) -> Me:
    guards = get_guards(request)
    profile = await asyncio.to_thread(_profile, guards.engine, user_id)
    if profile is None:
        raise ApiError("unauthorized")
    name, email, language = profile
    return Me(
        display_name=name,
        email_masked=mask_email(email),
        language=language,
        quota=await guards.quota.statuses(user_id),
    )


@router.get("")
async def get_me(user: CurrentUser, request: Request) -> Me:
    return await _me(request, user.id)


@router.put("")
async def put_me(body: MeUpdate, user: CurrentUser, request: Request) -> Me:
    await asyncio.to_thread(_set_language, get_guards(request).engine, user.id, body.language)
    return await _me(request, user.id)
