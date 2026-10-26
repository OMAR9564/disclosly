"""Load `llm/providers.yaml` (contracts/model-router.md).

A provider whose `api_key_env` (or any `{VAR}` in its `base_url`) is unset is skipped.
A task class that names an unknown provider is a configuration error.
"""

import re
from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

from llm.types import TaskClass

_PLACEHOLDER_RE = re.compile(r"\{([A-Z0-9_]+)\}")


class ProviderConfigError(ValueError):
    """`providers.yaml` is malformed or inconsistent."""


class Price(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    input: float = Field(ge=0)
    output: float = Field(ge=0)


class ProviderSpec(BaseModel):
    """One provider entry as written in the YAML file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str
    model: str
    api_key_env: str
    daily_requests: int = Field(ge=1)
    native_tools: bool = True
    price_per_mtok: Price
    # Seconds to wait for one answer; slow hosted models need more than the default.
    timeout_seconds: float = Field(default=15.0, gt=0, le=120)


class ProvidersFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    classes: dict[TaskClass, list[str]]
    providers: dict[str, ProviderSpec]

    @model_validator(mode="after")
    def _known_providers(self) -> "ProvidersFile":
        for task_class, names in self.classes.items():
            unknown = [name for name in names if name not in self.providers]
            if unknown:
                raise ValueError(f"class {task_class!r} names unknown providers {unknown}")
        return self


class Provider(BaseModel):
    """A provider that can be called: key present, placeholders resolved."""

    model_config = ConfigDict(frozen=True)

    name: str
    base_url: str
    model: str
    api_key: SecretStr
    daily_requests: int
    native_tools: bool
    price_per_mtok: Price
    timeout_seconds: float = 15.0


class RouterConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    classes: dict[TaskClass, list[Provider]]
    skipped: list[str]

    def providers_for(self, task_class: TaskClass) -> list[Provider]:
        return self.classes.get(task_class, [])


def _resolve(name: str, spec: ProviderSpec, env: Mapping[str, str]) -> Provider | None:
    api_key = env.get(spec.api_key_env, "").strip()
    if not api_key:
        return None
    base_url = spec.base_url
    for variable in _PLACEHOLDER_RE.findall(spec.base_url):
        value = env.get(variable, "").strip()
        if not value:
            return None
        base_url = base_url.replace("{" + variable + "}", value)
    return Provider(
        name=name,
        base_url=base_url.rstrip("/"),
        model=spec.model,
        api_key=SecretStr(api_key),
        daily_requests=spec.daily_requests,
        native_tools=spec.native_tools,
        price_per_mtok=spec.price_per_mtok,
        timeout_seconds=spec.timeout_seconds,
    )


def parse_providers(raw: object, env: Mapping[str, str]) -> RouterConfig:
    try:
        parsed = ProvidersFile.model_validate(raw)
    except ValidationError as exc:
        raise ProviderConfigError(str(exc)) from None
    enabled: dict[str, Provider] = {}
    skipped: list[str] = []
    for name, spec in parsed.providers.items():
        provider = _resolve(name, spec, env)
        if provider is None:
            skipped.append(name)
        else:
            enabled[name] = provider
    classes = {
        task_class: [enabled[name] for name in names if name in enabled]
        for task_class, names in parsed.classes.items()
    }
    return RouterConfig(classes=classes, skipped=sorted(skipped))


def load_providers(path: str | Path, env: Mapping[str, str]) -> RouterConfig:
    """Read and validate the providers file; `env` supplies keys (usually `os.environ`)."""
    with Path(path).open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return parse_providers(raw, env)
