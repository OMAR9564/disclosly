"""Calculations the analyst asks for, resolved and run in the sandbox (research §R9, §R10).

The model names each input by citation. Input values are resolved here, never taken on trust:
a fact input (`f:`) takes the stored value with its scale, whatever the model typed; a chunk
input (`c:`) needs a `value` that the Phase 3 number matcher finds in the chunk text. All
calculations of a round run in one sandbox execution: each one's code becomes a function, so
one failure does not stop the others. The verifier re-runs the same batch with re-resolved
inputs.
"""

import json
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.answer import states_amount
from agent.graph.state import CALC_NAME, CODE_MAX_CHARS, Calculation, CalculationInput
from tools.types import SeenItem

MAX_CALCULATIONS = 8
MAX_INPUTS = 12
# The sandbox service takes at most 16 KiB of code per batch.
BATCH_MAX_CHARS = 16 * 1024
_ERROR_CHARS = 300


class CalcInputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=CALC_NAME.pattern)
    cite_id: str
    value: Decimal | None = None


class CalculationSpec(BaseModel):
    """What the analyst model writes; the `k:` id, input values and output are set in code."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=CALC_NAME.pattern)
    description: str = Field(min_length=1, max_length=500)
    inputs: list[CalcInputSpec] = Field(min_length=1, max_length=MAX_INPUTS)
    code: str = Field(min_length=1, max_length=CODE_MAX_CHARS)


@dataclass(frozen=True)
class Resolved:
    """One calculation ready to run, or already failed (`error`) before the sandbox."""

    spec: CalculationSpec
    inputs: list[CalculationInput]
    error: str | None = None


def resolve_input(spec: CalcInputSpec, evidence: Mapping[str, SeenItem]) -> Decimal | str:
    """The input's value, or why it cannot be used."""
    item = evidence.get(spec.cite_id)
    if item is None:
        return f"input {spec.name}: {spec.cite_id} is not in the evidence"
    if spec.cite_id.startswith("f:"):
        if item.value is None:
            return f"input {spec.name}: {spec.cite_id} has no value"
        return item.value * (item.scale or 1)
    if spec.cite_id.startswith("c:"):
        if spec.value is None:
            return f"input {spec.name}: a chunk input needs the value it reads"
        if not states_amount(item, spec.value):
            return f"input {spec.name}: {spec.value} is not in {spec.cite_id}"
        return spec.value
    return f"input {spec.name}: {spec.cite_id} cannot be a calculation input"


def resolve(specs: Sequence[CalculationSpec], evidence: Mapping[str, SeenItem]) -> list[Resolved]:
    resolved: list[Resolved] = []
    seen: set[str] = set()
    for spec in specs[:MAX_CALCULATIONS]:
        inputs: list[CalculationInput] = []
        errors: list[str] = []
        if spec.name in seen:
            errors.append(f"calculation {spec.name} is defined twice")
        seen.add(spec.name)
        if len({i.name for i in spec.inputs}) != len(spec.inputs):
            errors.append("input names must be unique")
        for item in spec.inputs:
            value = resolve_input(item, evidence)
            if isinstance(value, str):
                errors.append(value)
            else:
                inputs.append(CalculationInput(name=item.name, cite_id=item.cite_id, value=value))
        try:
            compile(spec.code, f"<{spec.name}>", "exec")
        except SyntaxError as exc:
            errors.append(f"SyntaxError: {exc.msg}")
        resolved.append(Resolved(spec, inputs, "; ".join(errors)[:_ERROR_CHARS] or None))
    return resolved


def batch(
    runnable: Sequence[tuple[str, str, Sequence[CalculationInput]]],
) -> tuple[str, dict[str, Decimal]]:
    """Code and inputs for one sandbox run of `(name, code, inputs)` calculations.

    Each calculation's code runs in its own function with its own `inputs`; it must assign a
    number to `result`. Failures print one JSON line `{"calc", "error"}` instead of stopping
    the batch. Sandbox input names are `c<index>_<input>`, so calculations never collide.
    """
    lines = ["import json as __json", "__outputs = {}", ""]
    values: dict[str, Decimal] = {}
    for index, (name, code, inputs) in enumerate(runnable):
        mapping = ", ".join(f"{i.name!r}: inputs[{f'c{index}_{i.name}'!r}]" for i in inputs)
        for i in inputs:
            values[f"c{index}_{i.name}"] = i.value
        lines += [
            f"def __calc_{index}(inputs):",
            textwrap.indent(code.rstrip(), "    "),
            "    return result",
            "",
            "try:",
            f"    __outputs[{name!r}] = float(__calc_{index}({{{mapping}}}))",
            "except BaseException as __exc:",
            f"    print(__json.dumps({{'calc': {name!r}, "
            "'error': (type(__exc).__name__ + ': ' + str(__exc))[:300]}))",
            "",
        ]
    lines.append("result = __outputs")
    return "\n".join(lines), values


def batch_errors(stdout: str) -> dict[str, str]:
    """`{"calc", "error"}` lines the batch printed for calculations that failed."""
    errors: dict[str, str] = {}
    for line in stdout.splitlines():
        try:
            data: Any = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("calc"), str):
            errors[data["calc"]] = str(data.get("error") or "failed")[:_ERROR_CHARS]
    return errors


def fits(code: str) -> bool:
    return len(code.encode("utf-8")) <= BATCH_MAX_CHARS


def to_calculation(
    resolved: Resolved,
    cite_id: str,
    outputs: Mapping[str, Decimal],
    errors: Mapping[str, str],
    run_error: str | None,
) -> Calculation:
    name = resolved.spec.name
    error = resolved.error or errors.get(name) or run_error
    output = None if error else outputs.get(name)
    if error is None and output is None:
        error = "the calculation produced no result"
    return Calculation(
        cite_id=cite_id,
        name=name,
        description=resolved.spec.description,
        inputs=resolved.inputs,
        code=resolved.spec.code,
        output=output,
        error=error,
    )
