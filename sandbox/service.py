"""The sandbox service: `POST /run` executes one calculation batch (contracts/sandbox.md).

    uvicorn sandbox.service:app --host 0.0.0.0 --port 8100

It is the only container with the Docker socket, sits on the internal `sandbox_net` network
with `api`, and never runs user code itself. The image is chosen at startup from the
environment (`SANDBOX_IMAGE`, else `ghcr.io/omar9564/disclosly-sandbox:<SANDBOX_IMAGE_TAG>`);
request fields cannot change it or the container spec. Two executions run at once.
"""

import asyncio
import base64
import json
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from sandbox.docker import DOCKER_SOCKET, DockerClient, DockerUnavailable

IMAGE_REPOSITORY = "ghcr.io/omar9564/disclosly-sandbox"
CODE_MAX_CHARS = 16 * 1024
PAYLOAD_MAX_BYTES = 64 * 1024
MAX_TIMEOUT_SECONDS = 20.0
CONCURRENCY = 2


def image_from_env(environ: Mapping[str, str]) -> str:
    image = environ.get("SANDBOX_IMAGE", "").strip()
    if image:
        return image
    return f"{IMAGE_REPOSITORY}:{environ.get('SANDBOX_IMAGE_TAG', 'main').strip() or 'main'}"


InputName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=CODE_MAX_CHARS)
    inputs: dict[InputName, Annotated[str, StringConstraints(max_length=64)]] = Field(
        default_factory=dict
    )
    timeout_s: float = Field(default=MAX_TIMEOUT_SECONDS, ge=1)

    @field_validator("timeout_s")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return min(value, MAX_TIMEOUT_SECONDS)

    def payload(self) -> str:
        body = json.dumps({"code": self.code, "inputs": self.inputs}, ensure_ascii=False)
        return base64.b64encode(body.encode("utf-8")).decode("ascii")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def create_app(
    docker: DockerClient | None = None, environ: Mapping[str, str] | None = None
) -> FastAPI:
    env = os.environ if environ is None else environ
    image = image_from_env(env)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = docker or DockerClient(socket=env.get("SANDBOX_DOCKER_SOCKET", DOCKER_SOCKET))
        app.state.docker = client
        app.state.slots = asyncio.Semaphore(CONCURRENCY)
        try:
            yield
        finally:
            if docker is None:
                await client.aclose()

    app = FastAPI(title="disclosly-sandbox", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(422, "validation_error", "invalid run request")

    @app.get("/healthz")
    async def healthz(request: Request) -> JSONResponse:
        client: DockerClient = request.app.state.docker
        if not await client.ping():
            return _error(503, "sandbox_unavailable", "docker is not reachable")
        if not await client.image_present(image):
            return _error(503, "sandbox_unavailable", f"sandbox image {image} is not present")
        return JSONResponse({"status": "ok", "image": image})

    @app.post("/run")
    async def run(body: RunRequest, request: Request) -> Any:
        payload = body.payload()
        if len(payload) > PAYLOAD_MAX_BYTES:
            return _error(422, "validation_error", "payload over 64 KiB")
        client: DockerClient = request.app.state.docker
        async with request.app.state.slots:
            try:
                result = await client.run(image, payload, body.timeout_s)
            except DockerUnavailable as exc:
                return _error(503, "sandbox_unavailable", str(exc))
        return result.as_dict()

    return app


app = create_app()
