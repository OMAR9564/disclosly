"""Record graph cassettes from live providers (owner action; CI only replays).

    python -m evals.record_graph [--dataset deep_golden] [--case <id>] [--overwrite]
        [--remote-search redis://…] [--sandbox-image disclosly-sandbox:dev]

Needs `DATABASE_URL` (the real corpus), provider keys in the environment and the retrieval
models, like `evals.record_agent`; with `--remote-search` the searches go to a running search
worker through that Redis instead, so no local models are needed. Each case runs through the
graph engine; the proposed plan is approved automatically (plan-edit cases stop at the plan).
For injection cases the poisoned chunk is appended to the first document search or fact lookup
result. Calculations run on the local Docker daemon with the sandbox image and the service's
container spec (build it with `docker build -f sandbox/Dockerfile -t disclosly-sandbox:dev .`).
`verifier_seeded` drafts run through the verifier node alone. Cassettes hold only request
keys, responses, tool results and sandbox outputs; no headers are written.
"""

import argparse
import asyncio
import os
import sys
import time
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

import httpx

from api.search_client import RemoteSearch
from evals.agent_cases import InjectionCase, RecordedTool
from evals.graph_cases import (
    GRAPH_DATASETS,
    SEEDED,
    GraphCassette,
    RecordingSandbox,
    cassette_path,
    load_graph_dataset,
    recorded,
    run_graph_case,
    run_seeded_case,
    with_poison,
    write_cassette,
)
from evals.record_agent import ATTEMPTS, RETRY_WAIT_SECONDS, RecordingRouter
from ingestion.config import IngestionSettings
from ingestion.db import make_engine
from llm.config import load_providers
from llm.router import ModelRouter
from llm.types import ProvidersUnavailable, ToolCall
from retrieval.search import default_service
from sandbox.docker import DockerClient
from sandbox.service import RunRequest
from tools.python import Sandbox, SandboxRun
from tools.registry import GraphNode, NodeTools, ToolRegistry, ToolResult

POISON_TOOLS = frozenset({"search_documents", "get_financial_facts"})
DEFAULT_SANDBOX_IMAGE = "disclosly-sandbox:dev"


class DockerSandbox:
    """Calculation batches on the local Docker daemon, with the sandbox service's spec."""

    def __init__(self, image: str, client: DockerClient | None = None) -> None:
        self._image = image
        self._client = client or DockerClient()

    async def run(
        self, code: str, inputs: Mapping[str, Decimal], *, timeout_s: float
    ) -> SandboxRun:
        request = RunRequest(
            code=code, inputs={k: str(v) for k, v in inputs.items()}, timeout_s=timeout_s
        )
        result = await self._client.run(self._image, request.payload(), request.timeout_s)
        return SandboxRun(
            ok=result.ok,
            outputs={k: Decimal(v) for k, v in result.outputs.items()},
            stdout=result.stdout,
            error=result.error,
            duration_ms=result.duration_ms,
            timed_out=result.timed_out,
            oom=result.oom,
        )


class _RecordingView:
    def __init__(self, inner: NodeTools, box: "RecordingToolbox") -> None:
        self._inner = inner
        self._box = box
        self.names = inner.names

    @property
    def schemas(self) -> Any:
        return self._inner.schemas

    @property
    def tool_schema_version(self) -> str:
        return self._inner.tool_schema_version

    async def dispatch(self, call: ToolCall, language: str = "tr") -> ToolResult:
        result = await self._inner.dispatch(call, language)  # type: ignore[arg-type]
        poison = self._box.poison
        if poison is not None and call.name in POISON_TOOLS and result.error is None:
            result = with_poison(result, poison)
            self._box.poison = None
        self._box.recorded.append(recorded(call, result))
        return result


class RecordingToolbox:
    def __init__(self, registry: ToolRegistry, poison: InjectionCase | None) -> None:
        self._registry = registry
        self.poison = poison
        self.recorded: list[RecordedTool] = []

    def for_node(self, node: GraphNode) -> _RecordingView:
        return _RecordingView(self._registry.for_node(node), self)


async def record(
    datasets: Sequence[str],
    case_filter: str | None,
    *,
    overwrite: bool = False,
    retry_wait: float = RETRY_WAIT_SECONDS,
    remote_search: str | None = None,
    sandbox: Sandbox | None = None,
) -> tuple[int, list[str]]:
    """Record each case; skip cases already on disk unless `overwrite`."""
    calculations = sandbox or DockerSandbox(DEFAULT_SANDBOX_IMAGE)
    graph_datasets = [d for d in datasets if d != SEEDED]
    registry: Any = None
    if graph_datasets:
        settings = IngestionSettings()
        engine = make_engine(settings.require_database_url())
        search = RemoteSearch(remote_search) if remote_search else default_service()
        registry = ToolRegistry(search, engine)
    written = 0
    failed: list[str] = []
    async with httpx.AsyncClient(timeout=15.0) as http:
        live = ModelRouter(load_providers("llm/providers.yaml", os.environ), http)
        for name in datasets:
            for case in load_graph_dataset(name):  # type: ignore[arg-type]
                if case_filter and case.id != case_filter:
                    continue
                if not overwrite and cassette_path(case.id).is_file():
                    continue
                for attempt in range(1, ATTEMPTS + 1):
                    router = RecordingRouter(live)
                    poison = case if isinstance(case, InjectionCase) else None
                    toolbox = RecordingToolbox(registry, poison)
                    recording = RecordingSandbox(calculations)
                    started = time.perf_counter()
                    try:
                        if name == SEEDED:
                            verdict = await run_seeded_case(case, router, recording)
                            outcome = None
                        else:
                            outcome = await run_graph_case(case, router, toolbox, recording)
                        break
                    except ProvidersUnavailable as exc:
                        print(f"{case.id}: attempt {attempt} failed ({exc})", file=sys.stderr)
                        if attempt < ATTEMPTS:
                            await asyncio.sleep(retry_wait * attempt)
                else:
                    failed.append(case.id)
                    continue
                if outcome is None:
                    decision = None
                    status = "rejected" if not verdict.passed else "passed"
                else:
                    paused = outcome.paused_plan is not None
                    decision = None if paused or not outcome.values.get("plan") else "approve"
                    status = outcome.report.status if outcome.report else "paused"
                path = write_cassette(
                    GraphCassette(
                        case_id=case.id,
                        dataset=name,
                        interactions=router.interactions,
                        tools=toolbox.recorded,
                        sandbox=recording.records,
                        decision=decision,
                        elapsed_ms=round((time.perf_counter() - started) * 1000),
                    )
                )
                written += 1
                print(f"{case.id}: {status} -> {path}")
    return written, failed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.record_graph", description=__doc__)
    parser.add_argument("--dataset", choices=[*GRAPH_DATASETS, SEEDED], action="append")
    parser.add_argument("--case")
    parser.add_argument("--overwrite", action="store_true", help="re-record existing cassettes")
    parser.add_argument("--remote-search", help="Redis URL of a running search worker")
    parser.add_argument("--sandbox-image", default=DEFAULT_SANDBOX_IMAGE)
    args = parser.parse_args(argv)
    written, failed = asyncio.run(
        record(
            args.dataset or [*GRAPH_DATASETS, SEEDED],
            args.case,
            overwrite=args.overwrite,
            remote_search=args.remote_search,
            sandbox=DockerSandbox(args.sandbox_image),
        )
    )
    print(f"{written} cassettes written")
    if failed:
        print(f"{len(failed)} cases not recorded (rerun to retry): {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
