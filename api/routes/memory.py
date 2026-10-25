"""User memory endpoints (contracts/http-api.md, "Memory"; research §R13)."""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from agent import memory
from agent.answer import Language
from agent.graph.state import PeriodRange
from api.auth.session import CurrentUser
from api.errors import ApiError
from api.guards import get_guards, ip_rate_limit

router = APIRouter(prefix="/v1/me/memory", tags=["memory"], dependencies=[Depends(ip_rate_limit)])


class Preferences(BaseModel):
    answer_language: Language | None = None
    default_period_range: PeriodRange | None = None


class WatchlistAdd(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)


async def _memory(request: Request, user: CurrentUser) -> dict[str, Any]:
    return await asyncio.to_thread(memory.read_memory, get_guards(request).engine, user.id)


@router.get("")
async def get_memory(user: CurrentUser, request: Request) -> dict[str, Any]:
    return await _memory(request, user)


@router.put("/preferences")
async def put_preferences(body: Preferences, user: CurrentUser, request: Request) -> dict[str, Any]:
    await asyncio.to_thread(
        memory.set_preferences,
        get_guards(request).engine,
        user.id,
        body.answer_language,
        body.default_period_range,
    )
    return await _memory(request, user)


@router.post("/watchlist")
async def add_watchlist(body: WatchlistAdd, user: CurrentUser, request: Request) -> dict[str, Any]:
    try:
        await asyncio.to_thread(
            memory.add_to_watchlist, get_guards(request).engine, user.id, body.ticker
        )
    except memory.MemoryRejected as exc:
        raise ApiError(exc.code) from None
    return await _memory(request, user)


@router.delete("/watchlist/{ticker}")
async def remove_watchlist(ticker: str, user: CurrentUser, request: Request) -> dict[str, Any]:
    await asyncio.to_thread(
        memory.remove_from_watchlist, get_guards(request).engine, user.id, ticker[:12]
    )
    return await _memory(request, user)


@router.delete("", status_code=204)
async def delete_memory(user: CurrentUser, request: Request) -> Response:
    await asyncio.to_thread(memory.delete_all, get_guards(request).engine, user.id)
    return Response(status_code=204)
