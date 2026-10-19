# syntax=docker/dockerfile:1
# Background worker (arq): ingestion, OCR and small-batch embedding (PROJECT_SPEC.md §16.1).
# Build from the repository root: docker build -f workers/Dockerfile .
# workers/Dockerfile.dockerignore limits the build context.

FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

FROM python:3.12-slim AS builder
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
# The `ml` group brings sentence-transformers with CPU-only torch (uv index in pyproject.toml).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --group ml --no-install-project

FROM builder AS models
ARG EMBEDDING_MODEL=intfloat/multilingual-e5-small
ARG RERANK_MODEL=cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
ENV HF_HOME=/opt/models
# Models are downloaded at build time, so the running worker needs no model download.
RUN /opt/venv/bin/python -c "\
from sentence_transformers import CrossEncoder, SentenceTransformer; \
SentenceTransformer('${EMBEDDING_MODEL}', device='cpu'); \
CrossEncoder('${RERANK_MODEL}', device='cpu')"

FROM python:3.12-slim AS runtime
ENV PATH=/opt/venv/bin:${PATH} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/models \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    INGEST_DATA_DIR=/data/ingest
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-tur \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && install -d -o app -g app /data/ingest
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=models /opt/models /opt/models
COPY api ./api
COPY ingestion ./ingestion
COPY retrieval ./retrieval
COPY workers ./workers
USER app
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
    CMD ["arq", "--check", "workers.jobs.WorkerSettings"]
CMD ["arq", "workers.jobs.WorkerSettings"]
