"""In-memory stand-ins for the Redis commands the router and cache use."""

from typing import Any

import httpx
from pydantic import SecretStr

from llm.config import Price, Provider, RouterConfig

OK = {"choices": [{"message": {"role": "assistant", "content": "hi"}}], "usage": {
    "prompt_tokens": 1000, "completion_tokens": 500}}  # fmt: skip


class FakeStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, name: str) -> Any:
        return self.values.get(name)

    async def set(self, name: str, value: str, ex: int | None = None) -> Any:
        self.values[name] = value
        if ex is not None:
            self.ttls[name] = ex
        return True

    async def incr(self, name: str) -> Any:
        self.values[name] = str(int(self.values.get(name, "0")) + 1)
        return int(self.values[name])

    async def expire(self, name: str, time: int) -> Any:
        self.ttls[name] = time
        return True


def provider(name: str, daily_requests: int = 10) -> Provider:
    return Provider(
        name=name,
        base_url=f"https://{name}.test/v1",
        model=f"{name}-model",
        api_key=SecretStr("k"),
        daily_requests=daily_requests,
        native_tools=True,
        price_per_mtok=Price(input=0.1, output=0.4),
    )


def config(*names: str, daily_requests: int = 10) -> RouterConfig:
    return RouterConfig(
        classes={"cheap": [provider(n, daily_requests) for n in names], "strong": []},
        skipped=[],
    )


def http(responses: dict[str, httpx.Response | Exception], hosts: list[str]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host.split(".")[0]
        hosts.append(host)
        result = responses.get(host)
        if isinstance(result, Exception):
            raise result
        return result if result is not None else httpx.Response(200, json=OK)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))
