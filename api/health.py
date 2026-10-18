"""Liveness and readiness endpoints (contracts: HealthStatus, ReadinessStatus)."""

import asyncio
import errno
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Literal

import psycopg
import redis.asyncio
import redis.exceptions
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from api.config import Settings

logger = logging.getLogger(__name__)

router = APIRouter()

PROBE_TIMEOUT_S = 2.0

DependencyName = Literal["postgres", "redis"]
DependencyError = Literal["timeout", "connection_refused", "auth_failed", "unknown"]
Probe = Callable[[], Awaitable[None]]

_AUTH_MARKERS = ("password authentication failed", "noauth", "wrongpass")


class HealthStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok"] = "ok"
    version: str
    service: Literal["disclosly-api"] = "disclosly-api"


class DependencyCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok", "error"]
    latency_ms: float = Field(ge=0)
    error: DependencyError | None


class ReadinessStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ready", "not_ready"]
    version: str
    checks: dict[DependencyName, DependencyCheck]


def classify_error(exc: BaseException) -> DependencyError:
    """Map a driver exception to a stable code. Never exposes the exception text.

    Drivers often wrap the root cause (redis-py raises a generic ConnectionError from an
    OSError), so the whole `__cause__`/`__context__` chain is inspected.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        code = _classify_one(current)
        if code != "unknown":
            return code
        current = current.__cause__ or current.__context__
    return "unknown"


def _classify_one(exc: BaseException) -> DependencyError:
    message = str(exc).lower()
    if isinstance(exc, TimeoutError | redis.exceptions.TimeoutError):
        return "timeout"
    if isinstance(
        exc,
        psycopg.errors.InvalidPassword
        | psycopg.errors.InvalidAuthorizationSpecification
        | redis.exceptions.AuthenticationError,
    ) or any(marker in message for marker in _AUTH_MARKERS):
        return "auth_failed"
    if (
        isinstance(exc, ConnectionRefusedError)
        or (isinstance(exc, OSError) and exc.errno == errno.ECONNREFUSED)
        or "connection refused" in message
        or f"[errno {errno.ECONNREFUSED}]" in message
    ):
        return "connection_refused"
    return "unknown"


def default_probes(settings: Settings) -> dict[DependencyName, Probe]:
    async def postgres() -> None:
        async with await psycopg.AsyncConnection.connect(
            settings.database_url.get_secret_value(),
            connect_timeout=int(PROBE_TIMEOUT_S),
            autocommit=True,
        ) as conn:
            await conn.execute("SELECT 1")

    async def redis_ping() -> None:
        client = redis.asyncio.from_url(
            settings.redis_url,
            socket_connect_timeout=PROBE_TIMEOUT_S,
            socket_timeout=PROBE_TIMEOUT_S,
        )
        try:
            await client.ping()
        finally:
            await client.aclose()

    return {"postgres": postgres, "redis": redis_ping}


async def _run_check(name: DependencyName, probe: Probe) -> DependencyCheck:
    started = time.perf_counter()
    error: DependencyError | None = None
    try:
        await asyncio.wait_for(probe(), timeout=PROBE_TIMEOUT_S)
    except Exception as exc:
        error = classify_error(exc)
        logger.warning(
            "dependency check failed",
            extra={"dependency": name, "error": error, "exception_type": type(exc).__name__},
        )
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    return DependencyCheck(
        status="ok" if error is None else "error", latency_ms=latency_ms, error=error
    )


@router.get("/healthz", operation_id="getHealth")
async def get_health(request: Request) -> HealthStatus:
    """Answer if the process is alive. Does no dependency I/O by design."""
    # TEMPORARY rollback drill (quickstart V7, tasks.md T034). Revert right after the drill.
    if request.app.state.settings.app_env == "production":
        raise HTTPException(status_code=500, detail="rollback drill")
    return HealthStatus(version=request.app.state.settings.app_version)


@router.get(
    "/readyz",
    operation_id="getReadiness",
    response_model=ReadinessStatus,
    responses={503: {"model": ReadinessStatus}},
)
async def get_readiness(request: Request) -> JSONResponse:
    """Check Postgres and Redis concurrently; 503 unless both are reachable."""
    probes: dict[DependencyName, Probe] = request.app.state.readiness_probes
    names = list(probes)
    results = await asyncio.gather(*(_run_check(name, probes[name]) for name in names))
    checks = dict(zip(names, results, strict=True))
    ready = all(check.status == "ok" for check in checks.values())
    body = ReadinessStatus(
        status="ready" if ready else "not_ready",
        version=request.app.state.settings.app_version,
        checks=checks,
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=200 if ready else 503)
