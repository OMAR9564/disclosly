"""Fallback demo (T030): replayed provider responses, first provider forced to 429."""

import json
from pathlib import Path

import httpx
import pytest

from llm.demo_fallback import main, run_demo
from tests.unit.llm.fakes import config

OK = {"choices": [{"message": {"role": "assistant", "content": "A cash flow statement..."}}],
      "usage": {"prompt_tokens": 20, "completion_tokens": 9}}  # fmt: skip


def _replay(hosts: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(200, json=OK)

    return httpx.MockTransport(handler)


async def test_fail_first_falls_back_to_the_next_provider() -> None:
    hosts: list[str] = []
    result = await run_demo(config("a", "b"), transport=_replay(hosts))

    assert result.injected_into == "a"
    assert result.response.provider == "b"
    assert result.response.fallback_from == ["a"]
    assert hosts == ["b.test"]  # the injected 429 never reached the network


async def test_without_fail_first_the_first_provider_answers() -> None:
    hosts: list[str] = []
    result = await run_demo(config("a", "b"), fail_first=False, transport=_replay(hosts))

    assert result.response.provider == "a"
    assert result.response.fallback_from == []


def test_cli_without_keys_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    providers = tmp_path / "providers.yaml"
    providers.write_text(json.dumps({
        "classes": {"cheap": ["a"], "strong": []},
        "providers": {"a": {"base_url": "https://a.test/v1", "model": "m",
                            "api_key_env": "DEMO_UNSET_KEY", "daily_requests": 1,
                            "native_tools": True, "price_per_mtok": {"input": 0, "output": 0}}},
    }))  # fmt: skip
    monkeypatch.delenv("DEMO_UNSET_KEY", raising=False)
    with pytest.raises(SystemExit, match="no providers"):
        main(["--fail-first", "--providers", str(providers)])
