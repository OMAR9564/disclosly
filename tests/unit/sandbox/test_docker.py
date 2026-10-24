"""sandbox.docker (T031) against a mocked Docker Engine API."""

import json
import struct
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from sandbox import docker
from sandbox.docker import DockerClient, DockerUnavailable, RunResult

IMAGE = "ghcr.io/omar9564/disclosly-sandbox:abc"
ANSWER = {"outputs": {"ratio": "1.9"}, "error": None, "stdout": "hi"}


def frame(stream: int, data: bytes) -> bytes:
    return struct.pack(">BxxxL", stream, len(data)) + data


def logs_of(answer: dict[str, Any] | None, *, extra: bytes = b"") -> bytes:
    line = (json.dumps(answer) + "\n").encode() if answer is not None else b""
    return frame(2, b"warning\n") + frame(1, extra + line)


class Engine:
    """Records calls; `wait` can hang to exercise the timeout path."""

    def __init__(
        self,
        *,
        logs: bytes = b"",
        state: dict[str, Any] | None = None,
        create_status: int = 201,
        hang: bool = False,
        start_status: int = 204,
        wait_status: int = 200,
    ) -> None:
        self.calls: list[tuple[str, str]] = []
        self.created: dict[str, Any] | None = None
        self.logs = logs
        self.state = state or {"OOMKilled": False, "ExitCode": 0}
        self.create_status = create_status
        self.hang = hang
        self.start_status = start_status
        self.wait_status = wait_status

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if path.startswith("/images/"):
            return httpx.Response(200 if IMAGE in path else 404, json={})
        if path == "/containers/create":
            self.created = json.loads(request.content)
            if self.create_status != 201:
                return httpx.Response(self.create_status, json={"message": "No such image"})
            return httpx.Response(201, json={"Id": "c1", "Warnings": []})
        if path.endswith("/start"):
            return httpx.Response(self.start_status)
        if path.endswith("/wait"):
            if self.hang:
                import asyncio

                await asyncio.sleep(10)
            return httpx.Response(self.wait_status, json={"StatusCode": 0})
        if path.endswith("/kill"):
            return httpx.Response(204)
        if path.endswith("/json"):
            return httpx.Response(200, json={"State": self.state})
        if path.endswith("/logs"):
            return httpx.Response(200, content=self.logs)
        if request.method == "DELETE":
            return httpx.Response(204)
        if path == "/_ping":
            return httpx.Response(200, text="OK")
        return httpx.Response(500)


def client(engine: Callable[[httpx.Request], Any]) -> DockerClient:
    return DockerClient(transport=httpx.MockTransport(engine))


async def test_create_body_is_the_fixed_spec() -> None:
    engine = Engine(logs=logs_of(ANSWER))
    result = await client(engine).run(IMAGE, "UEFZTE9BRA==", 20)

    assert result == RunResult(
        ok=True, outputs={"ratio": "1.9"}, stdout="hi", duration_ms=result.duration_ms
    )
    assert engine.created == {
        "Image": IMAGE,
        "Cmd": ["python", "-I", "/runner/run.py"],
        "Entrypoint": [],
        "Env": ["SANDBOX_PAYLOAD=UEFZTE9BRA=="],
        "User": "65534:65534",
        "WorkingDir": "/tmp",  # noqa: S108
        "NetworkDisabled": True,
        "AttachStdin": False,
        "AttachStdout": False,
        "AttachStderr": False,
        "Tty": False,
        "OpenStdin": False,
        "Labels": {"disclosly.sandbox": "1"},
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Memory": 536870912,
            "MemorySwap": 536870912,
            "NanoCpus": 1000000000,
            "PidsLimit": 64,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=16m"},  # noqa: S108
            "Privileged": False,
            "AutoRemove": False,
            "Binds": [],
            "Mounts": [],
            "Devices": [],
            "PortBindings": {},
            "PublishAllPorts": False,
            "LogConfig": {"Type": "json-file", "Config": {"max-size": "1m"}},
        },
    }
    assert [c[0] + " " + c[1] for c in engine.calls] == [
        "POST /containers/create",
        "POST /containers/c1/start",
        "POST /containers/c1/wait",
        "GET /containers/c1/json",
        "GET /containers/c1/logs",
        "DELETE /containers/c1",
    ]


async def test_timeout_kills_and_removes(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = Engine(hang=True)
    result = await client(engine).run(IMAGE, "p", 0.05)
    assert (result.ok, result.timed_out, result.error) == (False, True, "timed out after 0.05 s")
    methods = [c[0] + " " + c[1] for c in engine.calls]
    assert "POST /containers/c1/kill" in methods
    assert methods[-1] == "DELETE /containers/c1"


async def test_oom_is_reported() -> None:
    engine = Engine(state={"OOMKilled": True, "ExitCode": 137})
    result = await client(engine).run(IMAGE, "p", 20)
    assert (result.ok, result.oom, result.error) == (False, True, "out of memory")


async def test_runner_error_is_passed_through() -> None:
    answer = {"outputs": {}, "error": "ZeroDivisionError: division by zero", "stdout": ""}
    result = await client(Engine(logs=logs_of(answer))).run(IMAGE, "p", 20)
    assert (result.ok, result.error, result.outputs) == (
        False,
        "ZeroDivisionError: division by zero",
        {},
    )


async def test_last_line_wins_over_code_output() -> None:
    logs = logs_of(ANSWER, extra=b'{"outputs": {"fake": "1"}, "error": null}\n')
    result = await client(Engine(logs=logs)).run(IMAGE, "p", 20)
    assert result.outputs == {"ratio": "1.9"}


@pytest.mark.parametrize(
    "logs",
    [
        b"",
        frame(1, b"not json\n"),
        frame(1, b"[1]\n"),
        frame(1, b'{"outputs": {"a": 1}}\n'),
    ],
)
async def test_missing_or_bad_answer(logs: bytes) -> None:
    result = await client(Engine(logs=logs, state={"ExitCode": 1})).run(IMAGE, "p", 20)
    assert (result.ok, result.error) == (False, "no result (exit code 1)")


async def test_output_over_64_kib_fails() -> None:
    logs = frame(1, b"x" * (70 * 1024))
    result = await client(Engine(logs=logs)).run(IMAGE, "p", 20)
    assert (result.ok, result.error) == (False, "output over 64 KiB")
    small_frames = b"".join(frame(1, b"y" * 1024) for _ in range(70))
    result = await client(Engine(logs=small_frames)).run(IMAGE, "p", 20)
    assert (result.ok, result.error) == (False, "output over 64 KiB")


async def test_missing_image_and_failed_steps_are_unavailable() -> None:
    with pytest.raises(DockerUnavailable, match="not present"):
        await client(Engine(create_status=404)).run(IMAGE, "p", 20)
    with pytest.raises(DockerUnavailable, match="create failed"):
        await client(Engine(create_status=500)).run(IMAGE, "p", 20)
    engine = Engine(start_status=500)
    with pytest.raises(DockerUnavailable, match="start failed"):
        await client(engine).run(IMAGE, "p", 20)
    assert engine.calls[-1] == ("DELETE", "/containers/c1")
    with pytest.raises(DockerUnavailable, match="wait failed"):
        await client(Engine(wait_status=500)).run(IMAGE, "p", 20)


async def test_unreachable_daemon() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no socket", request=request)

    docker_client = client(refuse)
    with pytest.raises(DockerUnavailable, match="ConnectError"):
        await docker_client.run(IMAGE, "p", 20)
    assert await docker_client.ping() is False
    await docker_client.aclose()


async def test_ping_and_image_present() -> None:
    docker_client = client(Engine())
    assert await docker_client.ping() is True
    assert await docker_client.image_present(IMAGE) is True
    assert await docker_client.image_present("other:tag") is False


def test_demultiplex_keeps_stdout_only() -> None:
    data = frame(1, b"a") + frame(2, b"err") + frame(1, b"b") + b"\x01\x00"
    assert docker.demultiplex(data) == b"ab"
    assert docker.parse_answer(b"\n\n") is None
