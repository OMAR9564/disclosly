"""Prometheus request metrics and GET /metrics (research.md R3: multiprocess mode).

With `PROMETHEUS_MULTIPROC_DIR` set, prometheus-client writes each worker's samples to files in
that directory and `/metrics` aggregates them, so every uvicorn worker reports the same totals.
"""

import time

from fastapi import APIRouter, Request, Response
from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_PLAIN_0_0_4
from prometheus_client.multiprocess import MultiProcessCollector
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.logging import route_template

REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "HTTP requests by method, route template and status code.",
    ["method", "route", "status"],
)
REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency by method and route template.",
    ["method", "route"],
)

router = APIRouter()


class MetricsMiddleware:
    """Records one counter sample and one latency sample per HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        status = 500
        start = time.perf_counter()

        async def send_with_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        except Exception:
            status = 500
            raise
        finally:
            # Unknown paths share one route label, so random URLs cannot grow the label set.
            route = route_template(scope)
            method = scope["method"]
            REQUESTS_TOTAL.labels(method=method, route=route, status=str(status)).inc()
            REQUEST_DURATION.labels(method=method, route=route).observe(time.perf_counter() - start)


@router.get("/metrics", operation_id="getMetrics", include_in_schema=False)
async def get_metrics(request: Request) -> Response:
    multiproc_dir: str | None = request.app.state.settings.prometheus_multiproc_dir
    if multiproc_dir:
        registry = CollectorRegistry()
        MultiProcessCollector(registry, path=multiproc_dir)  # type: ignore[no-untyped-call]
        payload = generate_latest(registry)
    else:
        payload = generate_latest(REGISTRY)
    return Response(content=payload, media_type=CONTENT_TYPE_PLAIN_0_0_4)
