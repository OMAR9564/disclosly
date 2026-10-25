"""Run one calculation batch inside the sandbox container (contracts/sandbox.md).

    python -I /runner/run.py

The payload comes base64-encoded in `SANDBOX_PAYLOAD`: `{"code": str, "inputs": {name:
decimal string}}`. Inputs are handed to the code as floats (pandas and numpy work on floats;
the verifier's tolerance is far wider than float error). The code runs in a fresh namespace
holding only `inputs` and must assign a dict of at most 100 finite numbers to `result`. The
runner prints exactly one JSON line `{"outputs", "error", "stdout"}`: outputs as decimal
strings, the last traceback line only as the error (at most 2 KiB), and what the code printed
(at most 4 KiB). The container is the security boundary; this file only shapes the output.

Standard library only: the image adds nothing but pandas and numpy.
"""

import base64
import binascii
import contextlib
import io
import json
import math
import os
import sys
import traceback
from decimal import Decimal, InvalidOperation
from typing import Any

PAYLOAD_ENV = "SANDBOX_PAYLOAD"
MAX_OUTPUTS = 100
ERROR_MAX_BYTES = 2048
STDOUT_MAX_BYTES = 4096
NAME_MAX_CHARS = 64


class RunnerError(Exception):
    """A payload or result problem reported as the run's error."""


def _clip(text: str, limit: int) -> str:
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return data[:limit].decode("utf-8", errors="ignore")


def load_payload(raw: str | None) -> tuple[str, dict[str, float]]:
    """The code and its inputs from the base64 JSON payload."""
    if not raw:
        raise RunnerError("no payload")
    try:
        payload = json.loads(base64.b64decode(raw, validate=True))
    except (binascii.Error, ValueError) as exc:
        raise RunnerError("payload is not base64 JSON") from exc
    if not isinstance(payload, dict):
        raise RunnerError("payload must be an object")
    code, inputs = payload.get("code"), payload.get("inputs", {})
    if not isinstance(code, str) or not code.strip():
        raise RunnerError("code must be a non-empty string")
    if not isinstance(inputs, dict):
        raise RunnerError("inputs must be an object")
    values: dict[str, float] = {}
    for name, value in inputs.items():
        if not isinstance(value, str):
            raise RunnerError(f"input {name!r} must be a decimal string")
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise RunnerError(f"input {name!r} is not a decimal") from exc
        if not number.is_finite():
            raise RunnerError(f"input {name!r} is not finite")
        values[str(name)] = float(number)
    return code, values


def to_decimal_string(name: str, value: Any) -> str:
    """One output as a decimal string; numpy and pandas scalars convert through `item()`."""
    if hasattr(value, "item") and callable(value.item):
        value = value.item()
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise RunnerError(f"result[{name!r}] must be a number, got {type(value).__name__}")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RunnerError(f"result[{name!r}] is not finite")
        return str(Decimal(repr(value)))
    if isinstance(value, Decimal) and not value.is_finite():
        raise RunnerError(f"result[{name!r}] is not finite")
    return str(value)


def collect(result: Any) -> dict[str, str]:
    if not isinstance(result, dict):
        raise RunnerError("the code must assign a dict to `result`")
    if len(result) > MAX_OUTPUTS:
        raise RunnerError(f"result has {len(result)} outputs; at most {MAX_OUTPUTS}")
    outputs = {}
    for name, value in result.items():
        if not isinstance(name, str) or not name or len(name) > NAME_MAX_CHARS:
            raise RunnerError("result keys must be names of 1-64 characters")
        outputs[name] = to_decimal_string(name, value)
    return outputs


def _last_line(exc: BaseException) -> str:
    lines = traceback.format_exception_only(type(exc), exc)
    return lines[-1].strip() if lines else type(exc).__name__


def execute(code: str, inputs: dict[str, float]) -> dict[str, Any]:
    """Run the code; never raises. The answer is what the runner prints."""
    printed = io.StringIO()
    namespace: dict[str, Any] = {"inputs": dict(inputs)}
    outputs: dict[str, str] = {}
    error: str | None = None
    try:
        compiled = compile(code, "<calculation>", "exec")
        with contextlib.redirect_stdout(printed):
            exec(compiled, namespace)  # noqa: S102  (the container is the sandbox)
        if "result" not in namespace:
            raise RunnerError("the code must assign a dict to `result`")
        outputs = collect(namespace["result"])
    except RunnerError as exc:
        error = str(exc)
    except BaseException as exc:  # SystemExit, MemoryError: all are run errors
        error = _last_line(exc)
    return {
        "outputs": outputs if error is None else {},
        "error": _clip(error, ERROR_MAX_BYTES) if error is not None else None,
        "stdout": _clip(printed.getvalue(), STDOUT_MAX_BYTES),
    }


def main(environ: dict[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    try:
        code, inputs = load_payload(env.get(PAYLOAD_ENV))
    except RunnerError as exc:
        answer: dict[str, Any] = {"outputs": {}, "error": str(exc), "stdout": ""}
    else:
        answer = execute(code, inputs)
    sys.stdout.write(json.dumps(answer, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0 if answer["error"] is None else 1


if __name__ == "__main__":
    sys.exit(main())
