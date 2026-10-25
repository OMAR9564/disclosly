"""Sandbox escape suite (T032, contracts/sandbox.md): the real image under the real Docker.

    docker build -f sandbox/Dockerfile -t disclosly-sandbox:dev .
    SANDBOX_IMAGE=disclosly-sandbox:dev uv run pytest -m sandbox

Every case must end with `ok = false` or have no effect. The service runs in-process with a
real Docker client; nothing is mocked.
"""

import ast
import os
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from sandbox.docker import DockerClient
from sandbox.service import create_app

pytestmark = pytest.mark.sandbox

IMAGE = os.environ.get("SANDBOX_IMAGE", "disclosly-sandbox:dev")
# Variables the base image and Docker set; anything else would have leaked from the host.
IMAGE_ENV = {
    "SANDBOX_PAYLOAD",
    "PATH",
    "HOSTNAME",
    "HOME",
    "LANG",
    "GPG_KEY",
    "PYTHON_VERSION",
    "PYTHON_SHA256",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONUNBUFFERED",
    "PIP_NO_CACHE_DIR",
    "PIP_DISABLE_PIP_VERSION_CHECK",
}


@pytest.fixture
async def sandbox() -> AsyncIterator[httpx.AsyncClient]:
    docker = DockerClient()
    if not await docker.ping():
        pytest.fail("Docker is not reachable; the sandbox suite needs /var/run/docker.sock")
    if not await docker.image_present(IMAGE):
        pytest.fail(f"build the sandbox image first: {IMAGE}")
    app = create_app(docker, {"SANDBOX_IMAGE": IMAGE})
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://sandbox", timeout=60
        ) as client:
            yield client
    leftovers = await docker._call(
        "GET",
        "/containers/json",
        params={"all": "1", "filters": '{"label": ["disclosly.sandbox=1"]}'},
    )
    await docker.aclose()
    assert leftovers.json() == [], "every sandbox container must be removed"


async def run(
    client: httpx.AsyncClient, code: str, timeout_s: float = 10, **inputs: str
) -> dict[str, Any]:
    response = await client.post(
        "/run", json={"code": code, "inputs": inputs, "timeout_s": timeout_s}
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_benign_pandas_calculation_is_fast(sandbox: httpx.AsyncClient) -> None:
    code = (
        "import pandas as pd\n"
        "s = pd.Series([inputs['q1'], inputs['q2'], inputs['q3'], inputs['q4']])\n"
        "result = {'growth': s.iloc[-1] / s.iloc[0] - 1, 'mean': s.mean()}"
    )
    started = time.monotonic()
    body = await run(sandbox, code, q1="35.1", q2="36.4", q3="38.5", q4="41.2")
    assert time.monotonic() - started < 3
    assert body["ok"] is True, body
    assert abs(float(body["outputs"]["growth"]) - (41.2 / 35.1 - 1)) < 1e-12


@pytest.mark.parametrize(
    ("name", "code"),
    [
        (
            "tcp to the internet",
            "import socket\nsocket.create_connection(('1.1.1.1', 53), timeout=2)",
        ),
        ("tcp to the api", "import socket\nsocket.create_connection(('api', 8000), timeout=2)"),
        ("dns", "import socket\nsocket.getaddrinfo('example.com', 443)"),
        ("read /etc/shadow", "open('/etc/shadow').read()"),
        ("read the docker socket", "open('/var/run/docker.sock', 'rb').read()"),
        ("write to /runner", "open('/runner/x', 'w').write('x')"),
        ("write to /", "open('/x', 'w').write('x')"),
        ("replace the runner", "open('/runner/run.py', 'w').write('x')"),
        (
            "execute from /tmp",
            (
                "import os, stat\n"
                "open('/tmp/x', 'w').write('#!/bin/sh\\necho hi')\n"
                "os.chmod('/tmp/x', stat.S_IRWXU)\n"
                "os.execv('/tmp/x', ['/tmp/x'])"
            ),
        ),
        ("import a module not in the image", "import requests"),
        ("raise privileges", "import os\nos.setuid(0)"),
    ],
)
async def test_blocked(sandbox: httpx.AsyncClient, name: str, code: str) -> None:
    body = await run(sandbox, code + "\nresult = {'escaped': 1}")
    assert body["ok"] is False, f"{name}: {body}"
    assert body["outputs"] == {}


async def test_fork_bomb_hits_the_pid_limit(sandbox: httpx.AsyncClient) -> None:
    code = (
        "import os, time\n"
        "for _ in range(200):\n"
        "    if os.fork() == 0:\n"
        "        time.sleep(5)\n"
        "        os._exit(0)\n"
        "result = {'forked': 200}"
    )
    body = await run(sandbox, code)
    assert body["ok"] is False
    assert "BlockingIOError" in (body["error"] or "") or body["timed_out"]


async def test_memory_is_capped(sandbox: httpx.AsyncClient) -> None:
    body = await run(sandbox, "x = b'x' * 1024 ** 3\nresult = {'n': len(x)}")
    assert body["ok"] is False
    assert body["oom"] or "MemoryError" in (body["error"] or "")


async def test_endless_loop_is_killed(sandbox: httpx.AsyncClient) -> None:
    started = time.monotonic()
    body = await run(sandbox, "while True:\n    pass", timeout_s=2)
    assert (body["ok"], body["timed_out"]) == (False, True)
    assert time.monotonic() - started < 10


async def test_large_prints_are_cut(sandbox: httpx.AsyncClient) -> None:
    captured = await run(sandbox, "print('x' * (10 * 1024 ** 2))\nresult = {'n': 1}")
    assert captured["ok"] is True
    assert len(captured["stdout"]) == 4096
    direct = await run(
        sandbox,
        "import sys\nsys.__stdout__.write('x' * (10 * 1024 ** 2))\nresult = {'n': 1}",
    )
    assert (direct["ok"], direct["error"]) == (False, "output over 64 KiB")


async def _names_from_error(sandbox: httpx.AsyncClient, code: str) -> set[str]:
    body = await run(sandbox, code)
    assert body["ok"] is False
    message = body["error"] or ""
    names = ast.literal_eval(message.split(": ", 1)[1])
    assert isinstance(names, list)
    return set(names)


async def test_environment_holds_only_the_payload(sandbox: httpx.AsyncClient) -> None:
    names = await _names_from_error(sandbox, "import os\nraise ValueError(sorted(os.environ))")
    assert "SANDBOX_PAYLOAD" in names
    assert names <= IMAGE_ENV, names - IMAGE_ENV


async def test_pid_1_environment_holds_only_the_payload(sandbox: httpx.AsyncClient) -> None:
    code = (
        "raw = open('/proc/1/environ', 'rb').read().split(b'\\0')\n"
        "raise ValueError(sorted(e.split(b'=')[0].decode() for e in raw if e))"
    )
    names = await _names_from_error(sandbox, code)
    assert names <= IMAGE_ENV, names - IMAGE_ENV


async def test_runs_as_nobody_without_capabilities(sandbox: httpx.AsyncClient) -> None:
    code = (
        "import os\n"
        "status = open('/proc/self/status').read().splitlines()\n"
        "caps = [l.split()[1] for l in status if l.startswith(('CapEff', 'CapPrm'))]\n"
        "nnp = [l.split()[1] for l in status if l.startswith('NoNewPrivs')]\n"
        "result = {'uid': os.getuid(), 'gid': os.getgid(), 'caps': int(''.join(caps), 16),\n"
        "          'no_new_privs': int(nnp[0])}"
    )
    body = await run(sandbox, code)
    assert body["ok"] is True, body
    assert body["outputs"] == {"uid": "65534", "gid": "65534", "caps": "0", "no_new_privs": "1"}
