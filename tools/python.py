"""`run_python`: one calculation batch in the sandbox service (contracts/sandbox.md, §R8).

The analyst and the verifier call it from code; the model never sees it as a tool. Each call
counts one sandbox run against the deep budget and is bounded by the run's remaining time.
The span records duration, status, timeout, OOM and the number of outputs; never the code or
the inputs.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import httpx
from opentelemetry import trace

from agent.budget import Clock, LimitReached, ModeBudget, Usage, take_sandbox_run, utcnow
from tools.types import SandboxUnavailable

SANDBOX_SPAN = "sandbox.run"
MAX_TIMEOUT_SECONDS = 20.0
MIN_TIMEOUT_SECONDS = 1.0
# Time the service needs around the container's own limit (create, logs, remove).
SERVICE_OVERHEAD_SECONDS = 5.0

_tracer = trace.get_tracer("tools.python")


class SandboxRejected(Exception):
    """The sandbox service refused the request (422): code or payload over its limits."""


@dataclass(frozen=True)
class SandboxRun:
    ok: bool
    outputs: dict[str, Decimal] = field(default_factory=dict)
    stdout: str = ""
    error: str | None = None
    duration_ms: int = 0
    timed_out: bool = False
    oom: bool = False


class Sandbox(Protocol):
    async def run(
        self, code: str, inputs: Mapping[str, Decimal], *, timeout_s: float
    ) -> SandboxRun: ...


def _decimal_outputs(raw: Any) -> dict[str, Decimal]:
    if not isinstance(raw, dict):
        return {}
    outputs = {}
    for name, value in raw.items():
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            continue
        if number.is_finite():
            outputs[str(name)] = number
    return outputs


class SandboxClient:
    """HTTP client for `POST {SANDBOX_URL}/run`."""

    def __init__(
        self,
        base_url: str,
        *,
        http: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient(transport=transport)
        self._owned = http is None

    async def aclose(self) -> None:
        if self._owned:
            await self._http.aclose()

    async def run(
        self, code: str, inputs: Mapping[str, Decimal], *, timeout_s: float
    ) -> SandboxRun:
        container_timeout = min(MAX_TIMEOUT_SECONDS, max(MIN_TIMEOUT_SECONDS, timeout_s))
        body = {
            "code": code,
            "inputs": {name: str(value) for name, value in inputs.items()},
            "timeout_s": container_timeout,
        }
        try:
            response = await self._http.post(
                f"{self._base_url}/run",
                json=body,
                timeout=max(timeout_s, MIN_TIMEOUT_SECONDS) + SERVICE_OVERHEAD_SECONDS,
            )
        except httpx.TimeoutException as exc:
            raise LimitReached("deadline") from exc
        except httpx.TransportError as exc:
            raise SandboxUnavailable(f"sandbox: {type(exc).__name__}") from exc
        if response.status_code == 422:
            raise SandboxRejected("the sandbox refused the calculation request")
        if response.status_code != 200:
            raise SandboxUnavailable(f"sandbox answered {response.status_code}")
        data = response.json()
        return SandboxRun(
            ok=bool(data.get("ok")),
            outputs=_decimal_outputs(data.get("outputs")) if data.get("ok") else {},
            stdout=str(data.get("stdout") or ""),
            error=data.get("error"),
            duration_ms=int(data.get("duration_ms") or 0),
            timed_out=bool(data.get("timed_out")),
            oom=bool(data.get("oom")),
        )


async def run_python(
    sandbox: Sandbox,
    code: str,
    inputs: Mapping[str, Decimal],
    *,
    usage: Usage,
    budget: ModeBudget,
    deadline_at: datetime | None,
    clock: Clock = utcnow,
    tracer: trace.Tracer | None = None,
) -> SandboxRun:
    """Run one batch; raises `LimitReached` (sandbox runs or deadline) or `SandboxUnavailable`."""
    take_sandbox_run(usage, budget)
    remaining = (
        MAX_TIMEOUT_SECONDS if deadline_at is None else (deadline_at - clock()).total_seconds()
    )
    if remaining <= 0:
        raise LimitReached("deadline")
    span = (tracer or _tracer).start_span(SANDBOX_SPAN)
    with trace.use_span(span, end_on_exit=True):
        try:
            result = await sandbox.run(code, inputs, timeout_s=remaining)
        except Exception as exc:
            span.set_attribute("disclosly.sandbox.status", type(exc).__name__)
            raise
        attributes: dict[str, str | int | bool] = {
            "disclosly.sandbox.status": "ok" if result.ok else "error",
            "disclosly.sandbox.duration_ms": result.duration_ms,
            "disclosly.sandbox.timed_out": result.timed_out,
            "disclosly.sandbox.oom": result.oom,
            "disclosly.sandbox.outputs": len(result.outputs),
        }
        for key, value in attributes.items():
            span.set_attribute(key, value)
        return result
