"""Corpus company search for the settings page (contracts/http-api.md, "Companies")."""

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from agent import memory
from api.auth.session import CurrentUser
from api.guards import get_guards, ip_rate_limit

router = APIRouter(
    prefix="/v1/companies", tags=["companies"], dependencies=[Depends(ip_rate_limit)]
)


@router.get("")
async def list_companies(
    user: CurrentUser,
    request: Request,
    q: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> dict[str, list[dict[str, Any]]]:
    items = await asyncio.to_thread(
        memory.search_companies, get_guards(request).engine, q.strip() if q else None, limit
    )
    return {"items": items}
