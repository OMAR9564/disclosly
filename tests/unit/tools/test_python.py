"""tools.python (T034): the sandbox HTTP client, budget, deadline and span."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest

from agent.budget import LimitReached, ModeBudget, Usage
from tests.unit.llm.test_telemetry import RecordingTracer
from tools.python import SANDBOX_SPAN, SandboxClient, SandboxRejected, SandboxRun, run_python
from tools.types import SandboxUnavailable

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)
BUDGET = ModeBudget(
    mode="deep",
    max_model_calls=20,
    deadline_seconds=300,
    max_tool_calls=24,
    max_steps=12,
    max_sandbox_runs=2,
)
OK = {
    "ok": True,
    "outputs": {"ratio": "1.9", "bad": "NaN"},
    "stdout": "",
    "error": None,
    "duration_ms": 812,
    "timed_out": False,
    "oom": False,
}


def _client(handler: Any) -> tuple[SandboxClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        result: httpx.Response = handler(request)
        return result

    return SandboxClient("http://sandbox:8100/", transport=httpx.MockTransport(record)), seen


async def test_posts_decimal_strings_and_reads_outputs() -> None:
    client, seen = _client(lambda r: httpx.Response(200, json=OK))
    usage = Usage()
    result = await run_python(
        client,
        "result = {'ratio': inputs['a'] / inputs['b']}",
        {"a": Decimal("41200000000"), "b": Decimal("21684210526")},
        usage=usage,
        budget=BUDGET,
        deadline_at=NOW + timedelta(seconds=90),
        clock=lambda: NOW,
    )
    assert result == SandboxRun(ok=True, outputs={"ratio": Decimal("1.9")}, duration_ms=812)
    assert usage.sandbox_runs == 1
    request = seen[0]
    assert str(request.url) == "http://sandbox:8100/run"
    assert json.loads(request.content) == {
        "code": "result = {'ratio': inputs['a'] / inputs['b']}",
        "inputs": {"a": "41200000000", "b": "21684210526"},
        "timeout_s": 20.0,
    }
    # The HTTP timeout follows the run's remaining time.
    assert request.extensions["timeout"]["read"] == 95.0
    await client.aclose()


async def test_short_remaining_time_shortens_the_container_timeout() -> None:
    client, seen = _client(lambda r: httpx.Response(200, json=OK))
    await run_python(
        client,
        "x",
        {},
        usage=Usage(),
        budget=BUDGET,
        deadline_at=NOW + timedelta(seconds=4),
        clock=lambda: NOW,
    )
    assert json.loads(seen[0].content)["timeout_s"] == 4.0
    assert seen[0].extensions["timeout"]["read"] == 9.0


async def test_failed_runs_carry_the_error() -> None:
    body = {**OK, "ok": False, "outputs": {}, "error": "ZeroDivisionError", "timed_out": True}
    client, _ = _client(lambda r: httpx.Response(200, json=body))
    result = await client.run("x", {}, timeout_s=10)
    assert (result.ok, result.error, result.timed_out, result.outputs) == (
        False,
        "ZeroDivisionError",
        True,
        {},
    )


@pytest.mark.parametrize("status", [503, 500])
async def test_unavailable_statuses(status: int) -> None:
    client, _ = _client(lambda r: httpx.Response(status, json={}))
    with pytest.raises(SandboxUnavailable):
        await client.run("x", {}, timeout_s=10)


async def test_connection_error_is_unavailable_and_timeout_is_the_deadline() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(SandboxUnavailable, match="ConnectError"):
        await _client(refuse)[0].run("x", {}, timeout_s=10)
    with pytest.raises(LimitReached) as caught:
        await _client(slow)[0].run("x", {}, timeout_s=10)
    assert caught.value.limit == "deadline"


async def test_422_is_rejected() -> None:
    client, _ = _client(lambda r: httpx.Response(422, json={}))
    with pytest.raises(SandboxRejected):
        await client.run("x", {}, timeout_s=10)


async def test_budget_and_deadline_stop_before_calling() -> None:
    client, seen = _client(lambda r: httpx.Response(200, json=OK))
    usage = Usage(sandbox_runs=2)
    with pytest.raises(LimitReached) as caught:
        await run_python(client, "x", {}, usage=usage, budget=BUDGET, deadline_at=None)
    assert caught.value.limit == "sandbox_runs"
    with pytest.raises(LimitReached) as caught:
        await run_python(
            client,
            "x",
            {},
            usage=Usage(),
            budget=BUDGET,
            deadline_at=NOW,
            clock=lambda: NOW,
        )
    assert caught.value.limit == "deadline"
    assert seen == []


async def test_span_has_status_and_counts_but_no_code_or_inputs() -> None:
    tracer = RecordingTracer()
    client, _ = _client(lambda r: httpx.Response(200, json=OK))
    await run_python(
        client,
        "secret_code = 1",
        {"secret_input": Decimal("7")},
        usage=Usage(),
        budget=BUDGET,
        deadline_at=None,
        tracer=tracer,
    )
    span = tracer.spans[0]
    assert span.name == SANDBOX_SPAN
    assert span.attributes == {
        "disclosly.sandbox.status": "ok",
        "disclosly.sandbox.duration_ms": 812,
        "disclosly.sandbox.timed_out": False,
        "disclosly.sandbox.oom": False,
        "disclosly.sandbox.outputs": 1,
    }
    assert span.ended
    assert "secret" not in repr(span.attributes)

    failing, _ = _client(lambda r: httpx.Response(503, json={}))
    with pytest.raises(SandboxUnavailable):
        await run_python(
            failing, "x", {}, usage=Usage(), budget=BUDGET, deadline_at=None, tracer=tracer
        )
    assert tracer.spans[1].attributes == {"disclosly.sandbox.status": "SandboxUnavailable"}
