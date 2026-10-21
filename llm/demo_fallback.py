"""Fallback demo (FR-028, SC-004): show a run surviving a provider failure.

    python -m llm.demo_fallback --fail-first [--task-class cheap] [--question "..."]

`--fail-first` answers the first provider's first request with a local 429, so the router must
cool it down and fall back. The output names the provider that answered, `fallback_from` and
the OTel trace id (when an SDK is configured). No cooldown or cache is shared with the API.
"""

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass

import httpx
from opentelemetry import trace

from llm.config import RouterConfig, load_providers
from llm.router import ModelRouter
from llm.types import ChatMessage, ChatRequest, ChatResponse, TaskClass

DEFAULT_QUESTION = "Reply with one short sentence: what is a cash flow statement?"
DEFAULT_PROVIDERS_FILE = "llm/providers.yaml"


class FailFirstTransport(httpx.AsyncBaseTransport):
    """Returns 429 for the first request to `host`, then delegates."""

    def __init__(self, inner: httpx.AsyncBaseTransport, host: str) -> None:
        self._inner = inner
        self._host = host
        self.injected = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not self.injected and request.url.host == self._host:
            self.injected = True
            return httpx.Response(429, headers={"Retry-After": "60"}, request=request)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


@dataclass(frozen=True)
class DemoResult:
    response: ChatResponse
    injected_into: str | None
    trace_id: str | None


async def run_demo(
    config: RouterConfig,
    *,
    task_class: TaskClass = "cheap",
    question: str = DEFAULT_QUESTION,
    fail_first: bool = True,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DemoResult:
    providers = config.providers_for(task_class)
    if not providers:
        raise SystemExit(f"no providers configured for {task_class}; set the API keys")
    inner = transport or httpx.AsyncHTTPTransport()
    injected_into = providers[0].name if fail_first else None
    if fail_first:
        inner = FailFirstTransport(inner, httpx.URL(providers[0].base_url).host)
    request = ChatRequest(messages=[ChatMessage(role="user", content=question)], max_tokens=128)
    tracer = trace.get_tracer("llm.demo_fallback")
    with tracer.start_as_current_span("disclosly.fallback_demo") as span:
        async with httpx.AsyncClient(transport=inner) as http:
            response = await ModelRouter(config, http).chat(request, task_class=task_class)
        context = span.get_span_context()
    trace_id = f"{context.trace_id:032x}" if context.is_valid else None
    return DemoResult(response, injected_into, trace_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m llm.demo_fallback")
    parser.add_argument("--fail-first", action="store_true")
    parser.add_argument("--task-class", choices=["cheap", "strong"], default="cheap")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--providers", default=DEFAULT_PROVIDERS_FILE)
    args = parser.parse_args(argv)

    config = load_providers(args.providers, os.environ)
    result = asyncio.run(
        run_demo(
            config,
            task_class=args.task_class,
            question=args.question,
            fail_first=args.fail_first,
        )
    )
    response = result.response
    print(f"injected 429 into: {result.injected_into or '-'}")
    print(f"answered by:       {response.provider} ({response.model})")
    print(f"fallback_from:     {', '.join(response.fallback_from) or '-'}")
    print(f"latency_ms:        {response.latency_ms}")
    print(f"tokens:            {response.tokens_in} in / {response.tokens_out} out")
    print(f"trace_id:          {result.trace_id or '- (no OTel SDK configured)'}")
    print(f"content:           {response.content}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
