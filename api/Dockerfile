# syntax=docker/dockerfile:1
# Build from the repository root: docker build -f api/Dockerfile --build-arg APP_VERSION=<sha> .

FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

FROM python:3.12-slim AS builder
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim AS runtime
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION} \
    PATH=/opt/venv/bin:${PATH} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini ./
COPY api ./api
# The research runtime imports these packages (Phase 3).
COPY agent ./agent
COPY llm ./llm
COPY tools ./tools
COPY retrieval ./retrieval
COPY ingestion ./ingestion
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
ENTRYPOINT ["/app/api/entrypoint.sh"]
CMD ["uvicorn", "api.main:create_app", "--factory", "--workers", "2", "--host", "0.0.0.0", "--port", "8000"]
