"""Every provider key in llm/providers.yaml reaches the api container through Compose.

Compose passes environment variables to `api` one by one, so a key set in the server `.env`
but missing from `infra/docker-compose.yml` is silently dropped and its provider skipped."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_compose_passes_every_provider_key() -> None:
    providers = yaml.safe_load((ROOT / "llm" / "providers.yaml").read_text(encoding="utf-8"))
    keys = {p["api_key_env"] for p in providers["providers"].values()}
    compose = (ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
    passed = set(re.findall(r"^\s+([A-Z][A-Z0-9_]+): \$\{\1(?::-[^}]*)?\}", compose, re.M))
    assert keys - passed == set()
