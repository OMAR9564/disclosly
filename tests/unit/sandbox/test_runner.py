"""The sandbox runner's logic, in-process (T030); the container limits are tested in T032."""

import base64
import json
from decimal import Decimal
from typing import Any

import pytest

from sandbox.runner import run


def _payload(code: str, inputs: dict[str, Any] | None = None) -> str:
    body = {"code": code, "inputs": inputs or {}}
    return base64.b64encode(json.dumps(body).encode()).decode()


def _main(code: str, inputs: dict[str, Any] | None = None, *, capsys: Any) -> dict[str, Any]:
    status = run.main({run.PAYLOAD_ENV: _payload(code, inputs)})
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    answer: dict[str, Any] = json.loads(lines[0])
    assert status == (0 if answer["error"] is None else 1)
    return answer


def test_ratio_from_decimal_inputs(capsys: pytest.CaptureFixture[str]) -> None:
    answer = _main(
        "result = {'ratio': inputs['net_debt'] / inputs['ebitda']}",
        {"net_debt": "41200000000", "ebitda": "20600000000"},
        capsys=capsys,
    )
    assert answer == {"outputs": {"ratio": "2.0"}, "error": None, "stdout": ""}


def test_inputs_are_floats_so_powers_work(capsys: pytest.CaptureFixture[str]) -> None:
    answer = _main(
        "result = {'cagr': (inputs['end'] / inputs['start']) ** (1 / 2) - 1}",
        {"start": "100", "end": "121"},
        capsys=capsys,
    )
    assert abs(Decimal(answer["outputs"]["cagr"]) - Decimal("0.1")) < Decimal("1e-12")


def test_print_is_captured_and_clipped(capsys: pytest.CaptureFixture[str]) -> None:
    answer = _main("print('x' * 10000)\nresult = {'n': 1}", capsys=capsys)
    assert answer["outputs"] == {"n": "1"}
    assert len(answer["stdout"]) == run.STDOUT_MAX_BYTES


@pytest.mark.parametrize(
    ("code", "error"),
    [
        ("x = 1", "the code must assign a dict to `result`"),
        ("result = [1]", "the code must assign a dict to `result`"),
        ("result = {'a': 'text'}", "result['a'] must be a number, got str"),
        ("result = {'a': True}", "result['a'] must be a number, got bool"),
        ("result = {'a': float('nan')}", "result['a'] is not finite"),
        ("result = {'a': float('inf')}", "result['a'] is not finite"),
        (
            "from decimal import Decimal\nresult = {'a': Decimal('NaN')}",
            "result['a'] is not finite",
        ),
        ("result = {i: 1 for i in range(3)}", "result keys must be names of 1-64 characters"),
        ("result = {f'k{i}': i for i in range(101)}", "result has 101 outputs; at most 100"),
        ("1 / 0", "ZeroDivisionError: division by zero"),
        ("import not_a_module", "ModuleNotFoundError: No module named 'not_a_module'"),
        ("raise SystemExit(3)", "SystemExit: 3"),
        ("def (", "SyntaxError: invalid syntax"),
    ],
)
def test_errors_are_one_line(code: str, error: str, capsys: pytest.CaptureFixture[str]) -> None:
    answer = _main(code, capsys=capsys)
    assert answer["outputs"] == {}
    assert answer["error"] == error


def test_scalars_with_item_are_unwrapped(capsys: pytest.CaptureFixture[str]) -> None:
    code = (
        "class Scalar:\n"
        "    def __init__(self, v): self.v = v\n"
        "    def item(self): return self.v\n"
        "result = {'a': Scalar(2.5), 'b': 7, 'c': __import__('decimal').Decimal('1.25')}"
    )
    assert _main(code, capsys=capsys)["outputs"] == {"a": "2.5", "b": "7", "c": "1.25"}


def test_long_errors_are_clipped(capsys: pytest.CaptureFixture[str]) -> None:
    answer = _main("raise ValueError('é' * 5000)", capsys=capsys)
    assert len(answer["error"].encode()) <= run.ERROR_MAX_BYTES
    assert answer["error"].startswith("ValueError: éé")


def test_namespace_holds_only_inputs(capsys: pytest.CaptureFixture[str]) -> None:
    code = "result = {'names': len([n for n in dir() if not n.startswith('__')])}"
    assert _main(code, {"a": "1"}, capsys=capsys)["outputs"] == {"names": "1"}


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        (None, "no payload"),
        ("", "no payload"),
        ("not base64!", "payload is not base64 JSON"),
        (base64.b64encode(b"[1]").decode(), "payload must be an object"),
        (base64.b64encode(b'{"code": ""}').decode(), "code must be a non-empty string"),
        (base64.b64encode(b'{"code": "x", "inputs": []}').decode(), "inputs must be an object"),
        (
            base64.b64encode(b'{"code": "x", "inputs": {"a": 1}}').decode(),
            "input 'a' must be a decimal string",
        ),
        (
            base64.b64encode(b'{"code": "x", "inputs": {"a": "abc"}}').decode(),
            "input 'a' is not a decimal",
        ),
        (
            base64.b64encode(b'{"code": "x", "inputs": {"a": "Infinity"}}').decode(),
            "input 'a' is not finite",
        ),
    ],
)
def test_bad_payloads(raw: str | None, error: str, capsys: pytest.CaptureFixture[str]) -> None:
    env = {} if raw is None else {run.PAYLOAD_ENV: raw}
    assert run.main(env) == 1
    assert json.loads(capsys.readouterr().out) == {"outputs": {}, "error": error, "stdout": ""}


def test_reads_the_process_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(run.PAYLOAD_ENV, _payload("result = {'a': 1}"))
    assert run.main() == 0
    assert json.loads(capsys.readouterr().out)["outputs"] == {"a": "1"}
