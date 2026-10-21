"""Tests for llm.config and llm/providers.yaml (T007)."""

from pathlib import Path
from typing import Any

import pytest

from llm.config import ProviderConfigError, load_providers, parse_providers

REPO_FILE = Path("llm/providers.yaml")
ALL_KEYS = {
    "GEMINI_API_KEY": "g",
    "GROQ_API_KEY": "q",
    "GITHUB_MODELS_TOKEN": "h",
    "OPENROUTER_API_KEY": "o",
    "CLOUDFLARE_AI_TOKEN": "c",
    "CLOUDFLARE_ACCOUNT_ID": "acct123",
}


def _raw(**overrides: Any) -> dict[str, Any]:
    provider = {
        "base_url": "https://example.test/v1/",
        "model": "m-1",
        "api_key_env": "A_KEY",
        "daily_requests": 100,
        "native_tools": True,
        "price_per_mtok": {"input": 0.1, "output": 0.2},
    }
    raw: dict[str, Any] = {
        "classes": {"cheap": ["a", "b"], "strong": ["b"]},
        "providers": {"a": dict(provider), "b": {**provider, "api_key_env": "B_KEY"}},
    }
    raw.update(overrides)
    return raw


def test_repo_file_has_every_provider_and_both_classes() -> None:
    config = load_providers(REPO_FILE, ALL_KEYS)

    cheap = [provider.name for provider in config.providers_for("cheap")]
    strong = [provider.name for provider in config.providers_for("strong")]
    assert cheap == [
        "gemini-flash-lite",
        "groq-llama",
        "groq-qwen",
        "openrouter-free",
        "github-mini",
        "cloudflare",
    ]
    assert strong == [
        "gemini-flash-stable",
        "gemini-flash",
        "openrouter-large",
        "groq-large",
        "github-large",
    ]
    assert config.skipped == []
    cloudflare = config.providers_for("cheap")[-1]
    assert "acct123" in cloudflare.base_url
    assert cloudflare.native_tools is False


def test_repo_file_with_no_keys_skips_everything() -> None:
    config = load_providers(REPO_FILE, {})
    assert config.providers_for("cheap") == []
    assert len(config.skipped) == 11


def test_provider_with_unset_key_is_skipped() -> None:
    config = parse_providers(_raw(), {"B_KEY": "secret-b"})

    assert [p.name for p in config.providers_for("cheap")] == ["b"]
    assert config.skipped == ["a"]
    assert config.providers_for("strong")[0].api_key.get_secret_value() == "secret-b"


def test_blank_key_counts_as_unset() -> None:
    config = parse_providers(_raw(), {"A_KEY": "  ", "B_KEY": "x"})
    assert config.skipped == ["a"]


def test_missing_base_url_placeholder_skips_provider() -> None:
    raw = _raw()
    raw["providers"]["a"]["base_url"] = "https://x.test/{ACCOUNT}/v1"
    config = parse_providers(raw, {"A_KEY": "k", "B_KEY": "k"})
    assert config.skipped == ["a"]

    config = parse_providers(raw, {"A_KEY": "k", "B_KEY": "k", "ACCOUNT": "42"})
    assert config.providers_for("cheap")[0].base_url == "https://x.test/42/v1"


def test_trailing_slash_trimmed() -> None:
    config = parse_providers(_raw(), {"A_KEY": "k"})
    assert config.providers_for("cheap")[0].base_url == "https://example.test/v1"


def test_unknown_provider_in_class_is_error() -> None:
    with pytest.raises(ProviderConfigError, match="unknown providers"):
        parse_providers(_raw(classes={"cheap": ["a", "nope"]}), {})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw["providers"]["a"].pop("model"),
        lambda raw: raw["providers"]["a"].update(daily_requests=0),
        lambda raw: raw["providers"]["a"].update(extra_field=1),
        lambda raw: raw["classes"].update(premium=["a"]),
    ],
)
def test_malformed_file_is_error(mutate: Any) -> None:
    raw = _raw()
    mutate(raw)
    with pytest.raises(ProviderConfigError):
        parse_providers(raw, {})


def test_api_key_not_in_repr() -> None:
    config = parse_providers(_raw(), {"A_KEY": "very-secret-key"})
    assert "very-secret-key" not in repr(config)
