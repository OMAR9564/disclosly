"""One calculation batch in a fresh container, over the Docker Engine API (contracts/sandbox.md).

The container spec is fixed here; callers pass only the image chosen at startup, the payload
and the timeout. The client talks to the Docker socket with `httpx` (no Docker SDK). Every
container is removed, whatever happened.
"""

import asyncio
import contextlib
import json
import struct
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

DOCKER_SOCKET = "/var/run/docker.sock"
# Unversioned paths use the daemon's own API version (Docker 29 dropped the old ones).
DOCKER_BASE_URL = "http://docker"
RUNNER_COMMAND = ["python", "-I", "/runner/run.py"]
PAYLOAD_ENV = "SANDBOX_PAYLOAD"
MEMORY_BYTES = 512 * 1024 * 1024
LOG_MAX_BYTES = 64 * 1024
STDOUT_MAX_CHARS = 4096
ERROR_MAX_CHARS = 2048
# Docker's own calls (create, kill, remove) get a short timeout of their own.
API_TIMEOUT_SECONDS = 15.0
_FRAME_HEADER = struct.Struct(">BxxxL")
# The container's own /tmp: a 16 MiB noexec tmpfs, the only writable path.
CONTAINER_TMP = "/tmp"  # noqa: S108


class DockerUnavailable(Exception):
    """The Docker daemon cannot be reached, or the sandbox image is missing."""


@dataclass(frozen=True)
class RunResult:
    ok: bool
    outputs: dict[str, str] = field(default_factory=dict)
    stdout: str = ""
    error: str | None = None
    duration_ms: int = 0
    timed_out: bool = False
    oom: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "outputs": self.outputs,
            "stdout": self.stdout,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "oom": self.oom,
        }


def container_spec(image: str, payload: str) -> dict[str, Any]:
    """The create body: no network, read-only root, 512 MiB, 1 CPU, 64 pids, no privileges."""
    return {
        "Image": image,
        "Cmd": RUNNER_COMMAND,
        "Entrypoint": [],
        "Env": [f"{PAYLOAD_ENV}={payload}"],
        "User": "65534:65534",
        "WorkingDir": CONTAINER_TMP,
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
            "Memory": MEMORY_BYTES,
            "MemorySwap": MEMORY_BYTES,
            "NanoCpus": 1_000_000_000,
            "PidsLimit": 64,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "Tmpfs": {CONTAINER_TMP: "rw,noexec,nosuid,size=16m"},
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


def demultiplex(data: bytes) -> bytes:
    """Stdout frames of a non-TTY log stream (8-byte header: stream, size); stderr is dropped."""
    out = bytearray()
    offset = 0
    while offset + _FRAME_HEADER.size <= len(data):
        stream, size = _FRAME_HEADER.unpack_from(data, offset)
        offset += _FRAME_HEADER.size
        if stream == 1:
            out += data[offset : offset + size]
        offset += size
    return bytes(out)


def parse_answer(stdout: bytes) -> dict[str, Any] | None:
    """The runner's JSON line: the last non-empty line of its stdout."""
    for line in reversed(stdout.decode("utf-8", errors="replace").splitlines()):
        if line.strip():
            try:
                answer = json.loads(line)
            except ValueError:
                return None
            return answer if isinstance(answer, dict) else None
    return None


def _outputs(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items()):
        return None
    return dict(raw)


class DockerClient:
    def __init__(
        self,
        *,
        socket: str = DOCKER_SOCKET,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = DOCKER_BASE_URL,
    ) -> None:
        self._client = httpx.AsyncClient(
            transport=transport or httpx.AsyncHTTPTransport(uds=socket),
            base_url=base_url,
            timeout=API_TIMEOUT_SECONDS,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._client.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise DockerUnavailable(f"docker: {type(exc).__name__}") from exc

    async def ping(self) -> bool:
        try:
            return (await self._call("GET", "/_ping")).status_code == 200
        except DockerUnavailable:
            return False

    async def image_present(self, image: str) -> bool:
        return (await self._call("GET", f"/images/{image}/json")).status_code == 200

    async def run(self, image: str, payload: str, timeout_s: float) -> RunResult:
        """Create, start, wait (kill at the timeout), read the logs, inspect, always remove."""
        created = await self._call(
            "POST", "/containers/create", json=container_spec(image, payload)
        )
        if created.status_code == 404:
            raise DockerUnavailable(f"sandbox image {image} is not present")
        if created.status_code != 201:
            raise DockerUnavailable(f"docker create failed ({created.status_code})")
        container = str(created.json()["Id"])
        started = time.monotonic()
        try:
            return await self._execute(container, timeout_s, started)
        finally:
            await self._remove(container)

    async def _execute(self, container: str, timeout_s: float, started: float) -> RunResult:
        start = await self._call("POST", f"/containers/{container}/start")
        if start.status_code not in (204, 304):
            raise DockerUnavailable(f"docker start failed ({start.status_code})")
        timed_out = False
        try:
            await asyncio.wait_for(self._wait(container, timeout_s), timeout=timeout_s)
        except TimeoutError:
            timed_out = True
            await self._call("POST", f"/containers/{container}/kill")
        duration_ms = round((time.monotonic() - started) * 1000)
        state = (await self._call("GET", f"/containers/{container}/json")).json().get("State", {})
        oom = bool(state.get("OOMKilled"))
        logs, overflow = await self._logs(container)
        if timed_out:
            return RunResult(
                ok=False,
                error=f"timed out after {timeout_s:g} s",
                duration_ms=duration_ms,
                timed_out=True,
                oom=oom,
            )
        if oom:
            return RunResult(ok=False, error="out of memory", duration_ms=duration_ms, oom=True)
        if overflow:
            return RunResult(ok=False, error="output over 64 KiB", duration_ms=duration_ms)
        answer = parse_answer(logs)
        outputs = _outputs(answer.get("outputs")) if answer else None
        if answer is None or outputs is None:
            code = state.get("ExitCode")
            return RunResult(
                ok=False, error=f"no result (exit code {code})", duration_ms=duration_ms
            )
        error = answer.get("error")
        error = str(error)[:ERROR_MAX_CHARS] if error is not None else None
        return RunResult(
            ok=error is None,
            outputs=outputs if error is None else {},
            stdout=str(answer.get("stdout") or "")[:STDOUT_MAX_CHARS],
            error=error,
            duration_ms=duration_ms,
        )

    async def _wait(self, container: str, timeout_s: float) -> None:
        response = await self._call(
            "POST",
            f"/containers/{container}/wait",
            params={"condition": "not-running"},
            timeout=timeout_s + API_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            raise DockerUnavailable(f"docker wait failed ({response.status_code})")

    async def _logs(self, container: str) -> tuple[bytes, bool]:
        """Stdout up to 64 KiB of raw stream, and whether there was more."""
        raw = bytearray()
        try:
            async with self._client.stream(
                "GET", f"/containers/{container}/logs", params={"stdout": "1", "stderr": "0"}
            ) as response:
                async for chunk in response.aiter_bytes():
                    raw += chunk
                    if len(raw) > LOG_MAX_BYTES + _FRAME_HEADER.size:
                        return demultiplex(bytes(raw[:LOG_MAX_BYTES])), True
        except httpx.TransportError as exc:
            raise DockerUnavailable(f"docker logs: {type(exc).__name__}") from exc
        stdout = demultiplex(bytes(raw))
        return stdout, len(stdout) > LOG_MAX_BYTES

    async def _remove(self, container: str) -> None:
        # A daemon that is gone has nothing left to clean from here.
        with contextlib.suppress(httpx.TransportError):
            await self._client.delete(f"/containers/{container}", params={"force": "true"})
