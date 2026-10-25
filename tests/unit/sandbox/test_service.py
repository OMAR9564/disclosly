"""sandbox.service (T031): request limits, image choice, concurrency and 503 mapping."""

import asyncio
import base64
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sandbox import service
from sandbox.docker import DockerUnavailable, RunResult


class FakeDocker:
    def __init__(
        self, *, fail: bool = False, delay: float = 0.0, up: bool = True, image: bool = True
    ) -> None:
        self.image = image
        self.runs: list[tuple[str, str, float]] = []
        self.fail = fail
        self.delay = delay
        self.up = up
        self.active = 0
        self.peak = 0

    async def run(self, image: str, payload: str, timeout_s: float) -> RunResult:
        self.runs.append((image, payload, timeout_s))
        if self.fail:
            raise DockerUnavailable("sandbox image x is not present")
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(self.delay)
        self.active -= 1
        return RunResult(ok=True, outputs={"ratio": "1.9"}, duration_ms=5)

    async def ping(self) -> bool:
        return self.up

    async def image_present(self, image: str) -> bool:
        return self.image

    async def aclose(self) -> None:
        return None


def _client(fake: FakeDocker, environ: dict[str, str] | None = None) -> TestClient:
    app = service.create_app(fake, environ or {"SANDBOX_IMAGE_TAG": "abc123"})  # type: ignore[arg-type]
    return TestClient(app)


BODY = {"code": "result = {'ratio': inputs['a'] / inputs['b']}", "inputs": {"a": "19", "b": "10"}}


def test_run_passes_the_payload_and_the_fixed_image() -> None:
    fake = FakeDocker()
    with _client(fake) as http:
        response = http.post("/run", json={**BODY, "timeout_s": 5})
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "outputs": {"ratio": "1.9"},
        "stdout": "",
        "error": None,
        "duration_ms": 5,
        "timed_out": False,
        "oom": False,
    }
    image, payload, timeout = fake.runs[0]
    assert image == "ghcr.io/omar9564/disclosly-sandbox:abc123"
    assert json.loads(base64.b64decode(payload)) == {"code": BODY["code"], "inputs": BODY["inputs"]}
    assert timeout == 5


@pytest.mark.parametrize(
    "extra",
    [
        {"image": "alpine"},
        {"HostConfig": {"Privileged": True}},
        {"env": {"X": "1"}},
    ],
)
def test_request_cannot_change_the_container(extra: dict[str, Any]) -> None:
    with _client(FakeDocker()) as http:
        response = http.post("/run", json={**BODY, **extra})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_timeout_is_clamped() -> None:
    fake = FakeDocker()
    with _client(fake) as http:
        assert http.post("/run", json={**BODY, "timeout_s": 300}).status_code == 200
        assert http.post("/run", json={**BODY, "timeout_s": 0.5}).status_code == 422
        assert http.post("/run", json=BODY).status_code == 200
    assert [r[2] for r in fake.runs] == [20.0, 20.0]


@pytest.mark.parametrize(
    "body",
    [
        {"code": ""},
        {"code": "x" * (16 * 1024 + 1)},
        {"code": "x", "inputs": {"bad name": "1"}},
        {"code": "x", "inputs": {"a": 1}},
    ],
)
def test_bad_requests(body: dict[str, Any]) -> None:
    with _client(FakeDocker()) as http:
        assert http.post("/run", json=body).status_code == 422


def test_oversize_payload_is_422() -> None:
    inputs = {f"v{i}": "1" * 60 for i in range(900)}
    fake = FakeDocker()
    with _client(fake) as http:
        response = http.post("/run", json={"code": "result = {}", "inputs": inputs})
    assert response.status_code == 422
    assert response.json()["error"]["message"] == "payload over 64 KiB"
    assert fake.runs == []


def test_docker_unavailable_is_503() -> None:
    with _client(FakeDocker(fail=True)) as http:
        response = http.post("/run", json=BODY)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "sandbox_unavailable"


def test_healthz() -> None:
    with _client(FakeDocker()) as http:
        assert http.get("/healthz").json()["status"] == "ok"
    with _client(FakeDocker(up=False)) as http:
        assert http.get("/healthz").status_code == 503
    with _client(FakeDocker(image=False)) as http:
        assert "not present" in http.get("/healthz").json()["error"]["message"]


async def test_two_runs_at_once() -> None:
    import httpx

    fake = FakeDocker(delay=0.05)
    app = service.create_app(fake, {})  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://sandbox") as http:
            responses = await asyncio.gather(*(http.post("/run", json=BODY) for _ in range(5)))
    assert all(r.status_code == 200 for r in responses)
    assert fake.peak == 2
    assert fake.runs[0][0] == "ghcr.io/omar9564/disclosly-sandbox:main"


def test_image_from_env() -> None:
    assert service.image_from_env({"SANDBOX_IMAGE": "disclosly-sandbox:dev"}) == (
        "disclosly-sandbox:dev"
    )
    assert service.image_from_env({"SANDBOX_IMAGE_TAG": " "}) == (
        "ghcr.io/omar9564/disclosly-sandbox:main"
    )
