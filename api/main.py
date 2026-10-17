"""App factory. Run with `uvicorn api.main:create_app --factory`."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import Settings, get_settings
from api.errors import register_exception_handlers
from api.health import default_probes
from api.health import router as health_router
from api.logging import REQUEST_ID_HEADER, RequestContextMiddleware, configure_logging
from api.metrics import MetricsMiddleware
from api.metrics import router as metrics_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    docs_enabled = settings.app_env == "development"
    app = FastAPI(
        title="Disclosly API",
        version=settings.app_version,
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.state.settings = settings
    app.state.readiness_probes = default_probes(settings)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(metrics_router)

    # Starlette runs the last added middleware first: request IDs wrap everything, CORS included.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_methods=["GET"],
        allow_headers=[REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestContextMiddleware, version=settings.app_version)
    return app
