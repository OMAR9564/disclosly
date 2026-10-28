"""Record agent cassettes from live providers (owner action; CI only replays).

    python -m evals.record_agent [--dataset answers_golden] [--case <id>] [--remote-search redis://…]

Needs `DATABASE_URL` (the real corpus), provider keys in the environment and the retrieval
models; with `--remote-search` the searches go to a running search worker through that Redis
instead. `--dataset deep_golden` records the single agent on the deep questions, which only
the single-vs-multi comparison (evals/compare.py) replays; their memory fixtures are ignored,
since the single agent has no memory. For injection cases the poisoned chunk is appended to
the first `search_documents` result. Cassettes hold only request keys, responses and tool
results; no headers are written.
"""

import argparse
import asyncio
import os
import sys
import time
from collections.abc import Sequence
from typing import Any

import httpx

from agent.loop import Budget, run_agent
from api.search_client import RemoteSearch
from evals.agent_cases import (
    DATASETS,
    Cassette,
    InjectionCase,
    RecordedTool,
    cassette_path,
    load_dataset,
    poisoned_item,
    tool_key,
    write_cassette,
)
from evals.graph_cases import load_graph_dataset
from ingestion.config import IngestionSettings
from ingestion.db import make_engine
from llm.config import load_providers
from llm.router import ModelRouter, Router
from llm.testing import interaction, unavailable_interaction
from llm.types import (
    ChatRequest,
    ChatResponse,
    ProvidersUnavailable,
    TaskClass,
    ToolCall,
    ToolSchema,
)
from retrieval.search import default_service
from tools.facts import Language
from tools.registry import ToolRegistry, ToolResult, wrap


class RecordingRouter:
    def __init__(self, inner: Router) -> None:
        self._inner = inner
        self.interactions: list[dict[str, Any]] = []

    async def chat(self, request: ChatRequest, *, task_class: TaskClass) -> ChatResponse:
        try:
            response = await self._inner.chat(request, task_class=task_class)
        except ProvidersUnavailable as exc:
            # A node may handle it (a re-ask falls back to a partial report): replayed as is.
            self.interactions.append(unavailable_interaction(request, task_class, exc))
            raise
        self.interactions.append(interaction(request, task_class, response))
        return response


class RecordingTools:
    def __init__(self, inner: ToolRegistry, poison: InjectionCase | None) -> None:
        self._inner = inner
        self._poison = poison
        self.recorded: list[RecordedTool] = []

    @property
    def schemas(self) -> list[ToolSchema]:
        return self._inner.schemas

    async def dispatch(self, call: ToolCall, language: Language = "tr") -> ToolResult:
        result = await self._inner.dispatch(call, language)
        if self._poison is not None and call.name == "search_documents" and result.error is None:
            item = poisoned_item(self._poison)
            items = [*result.items, item]
            content = "\n".join(wrap(i) for i in items)
            result = ToolResult(result.tool, content, result.summary, items, len(items))
            self._poison = None
        self.recorded.append(
            RecordedTool(
                key=tool_key(call.name, call.arguments),
                tool=result.tool,
                content=result.content,
                summary=result.summary,
                count=result.count,
                items=result.items,
                error=result.error,
            )
        )
        return result


async def _noop(event: str, data: dict[str, Any]) -> None:
    return None


DEEP = "deep_golden"
ATTEMPTS = 3
RETRY_WAIT_SECONDS = 30.0


async def record(
    datasets: Sequence[str],
    case_filter: str | None,
    *,
    overwrite: bool = False,
    retry_wait: float = RETRY_WAIT_SECONDS,
    remote_search: str | None = None,
) -> tuple[int, list[str]]:
    """Record each case; skip cases already on disk unless `overwrite`.

    A case whose providers are all unavailable is retried, then reported and skipped, so one
    busy provider does not lose the rest of the run. Rerun to fill the gaps.
    """
    settings = IngestionSettings()
    engine = make_engine(settings.require_database_url())
    search = RemoteSearch(remote_search) if remote_search else default_service()
    registry = ToolRegistry(search, engine)
    budget = Budget()
    written = 0
    failed: list[str] = []
    async with httpx.AsyncClient(timeout=15.0) as http:
        live = ModelRouter(load_providers("llm/providers.yaml", os.environ), http)
        for name in datasets:
            cases = load_graph_dataset(DEEP) if name == DEEP else load_dataset(name)  # type: ignore[arg-type]
            for case in cases:
                if "single" not in case.engines:
                    continue
                if case_filter and case.id != case_filter:
                    continue
                if not overwrite and cassette_path(case.id).is_file():
                    continue
                for attempt in range(1, ATTEMPTS + 1):
                    router = RecordingRouter(live)
                    poison = case if isinstance(case, InjectionCase) else None
                    tools = RecordingTools(registry, poison)
                    started = time.perf_counter()
                    try:
                        outcome = await run_agent(case.question, router, tools, budget, _noop)
                        break
                    except ProvidersUnavailable as exc:
                        print(f"{case.id}: attempt {attempt} failed ({exc})", file=sys.stderr)
                        if attempt < ATTEMPTS:
                            await asyncio.sleep(retry_wait * attempt)
                else:
                    failed.append(case.id)
                    continue
                path = write_cassette(
                    Cassette(
                        case_id=case.id,
                        dataset=name,
                        interactions=router.interactions,
                        tools=tools.recorded,
                        elapsed_ms=round((time.perf_counter() - started) * 1000),
                    )
                )
                written += 1
                print(f"{case.id}: {outcome.status} -> {path}")
    return written, failed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.record_agent", description=__doc__)
    parser.add_argument("--dataset", choices=[*DATASETS, DEEP], action="append")
    parser.add_argument("--case")
    parser.add_argument("--overwrite", action="store_true", help="re-record existing cassettes")
    parser.add_argument("--remote-search", help="Redis URL of a running search worker")
    args = parser.parse_args(argv)
    written, failed = asyncio.run(
        record(
            args.dataset or DATASETS,
            args.case,
            overwrite=args.overwrite,
            remote_search=args.remote_search,
        )
    )
    print(f"{written} cassettes written")
    if failed:
        print(f"{len(failed)} cases not recorded (rerun to retry): {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
