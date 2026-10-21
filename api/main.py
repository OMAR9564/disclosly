"""App factory. Run with `uvicorn api.main:create_app --factory`."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent.runs import RunService, mark_interrupted
from api.config import Settings, get_settings
from api.errors import register_exception_handlers
from api.health import default_probes
from api.health import router as health_router
from api.logging import REQUEST_ID_HEADER, RequestContextMiddleware, configure_logging
from api.metrics import MetricsMiddleware
from api.metrics import router as metrics_router
from api.routes.research import router as research_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the research runtime unless a test injected `app.state.run_service`."""
    resources: list[Any] = []
    if getattr(app.state, "run_service", None) is None:
        from api.runtime import build_run_service

        app.state.run_service, resources = build_run_service(app.state.settings)
    service: RunService = app.state.run_service
    try:
        await asyncio.to_thread(mark_interrupted, service.engine)
    except Exception:
        # The API must start even when the database is unreachable or not yet migrated;
        # /readyz reports the dependency, and the next startup retries the recovery.
        logger.warning("could not mark interrupted runs at startup", exc_info=True)
    try:
        yield
    finally:
        await service.wait_idle()
        for resource in resources:
            close = getattr(resource, "aclose", None)
            if close is not None:
                await close()
            else:
                resource.dispose()


def create_app(settings: Settings | None = None, run_service: RunService | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    docs_enabled = settings.app_env == "development"
    app = FastAPI(
        title="Disclosly API",
        version=settings.app_version,
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=_lifespan,
    )
    app.state.settings = settings
    app.state.run_service = run_service
    app.state.readiness_probes = default_probes(settings)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(research_router)

    # Starlette runs the last added middleware first: request IDs wrap everything, CORS included.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted({*settings.cors_allowed_origins, settings.web_origin}),
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=[REQUEST_ID_HEADER, "Content-Type", "Last-Event-ID", "Accept-Language"],
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestContextMiddleware, version=settings.app_version)
    return app
