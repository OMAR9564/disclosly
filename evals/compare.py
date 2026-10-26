"""Single agent vs multi-agent graph on the same cases (US6, SC-010; no network).

    python -m evals.run compare --replay [--reports DIR]

Replays every shared case (`answers_golden`, `deep_golden`, `advice_refusal`, `injection`
cases whose `engines` include both) with the `single` harness (evals/agent.py, cassettes in
`evals/cassettes/agent/`) and the `graph` harness (evals/graph.py, `evals/cassettes/graph/`),
scores both the same way, and writes `evals/reports/<date>-single-vs-multi.md` (and `.json`).

Per engine, over the cases both engines replayed: numeric match on `answers_golden` and on
`deep_golden`, citation coverage, the verified-number rate (derived golden numbers the answer
states that a verified calculation produced), refusal and injection pass rates, the partial
rate, model calls per run and recorded latency (cassettes recorded before latency was stored
have none, and the report says how many do). Each case is a win, a loss or a tie for the graph.
A case without a cassette for one engine is listed, not dropped. The run fails only when a
cassette is stale (the prompt or tools changed since recording); this is a report, not a gate.
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from agent.answer import figures
from agent.loop import Budget, run_agent
from evals import agent as single_harness
from evals import graph as graph_harness
from evals.agent_cases import (
    CASSETTES_DIR as SINGLE_CASSETTES_DIR,
)
from evals.agent_cases import (
    DATASETS_DIR,
    EVALS_DIR,
    AdviceCase,
    InjectionCase,
    Language,
    ToolCassetteMiss,
)
from evals.agent_cases import (
    cassette_path as single_cassette_path,
)
from evals.agent_cases import (
    read_cassette as read_single_cassette,
)
from evals.agent_cases import (
    replay as replay_single,
)
from evals.graph_cases import (
    CASSETTES_DIR as GRAPH_CASSETTES_DIR,
)
from evals.graph_cases import (
    DeepCase,
    GraphDataset,
    SandboxCassetteMiss,
    load_graph_dataset,
    replay_sandbox,
    run_graph_case,
)
from evals.graph_cases import (
    cassette_path as graph_cassette_path,
)
from evals.graph_cases import (
    read_cassette as read_graph_cassette,
)
from evals.graph_cases import (
    replay as replay_graph,
)
from evals.latency import percentile
from llm.testing import CassetteMiss

REPORTS_DIR = EVALS_DIR / "reports"
SHARED_DATASETS: tuple[GraphDataset, ...] = (
    "answers_golden",
    "deep_golden",
    "advice_refusal",
    "injection",
)
Engine = Literal["single", "graph"]
Outcome = Literal["win", "loss", "tie"]


@dataclass
class EngineResult:
    """One engine's score on one case, in the same terms for both engines."""

    status: str
    model_calls: int
    figures_total: int = 0
    figures_supported: int = 0
    numbers_expected: int = 0
    numbers_matched: int = 0
    derived_stated: int = 0
    derived_verified: int = 0
    # advice and injection cases: refused, or no forbidden output and every figure supported.
    passed: bool | None = None
    elapsed_ms: int | None = None

    def score(self) -> tuple[float, bool]:
        """(answer score, every figure and citation supported), compared across engines."""
        if self.passed is not None:
            value = 1.0 if self.passed else 0.0
        elif self.numbers_expected:
            value = self.numbers_matched / self.numbers_expected
        else:
            value = 1.0 if self.status not in ("partial", "failed") else 0.0
        return value, self.figures_supported == self.figures_total


@dataclass
class CaseRow:
    case_id: str
    dataset: str
    single: EngineResult | None = None
    graph: EngineResult | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def paired(self) -> bool:
        return self.single is not None and self.graph is not None

    def outcome(self) -> Outcome | None:
        """The graph's result on this case against the single agent's."""
        if self.single is None or self.graph is None:
            return None
        graph, single = self.graph.score(), self.single.score()
        if graph == single:
            return "tie"
        return "win" if graph > single else "loss"


@dataclass
class Comparison:
    rows: list[CaseRow] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)


def shared_cases(datasets_dir: Path = DATASETS_DIR) -> list[tuple[str, Any]]:
    """(dataset, case) for every case both engines answer."""
    cases: list[tuple[str, Any]] = []
    for name in SHARED_DATASETS:
        for case in load_graph_dataset(name, datasets_dir):
            if {"single", "graph"} <= set(case.engines):
                cases.append((name, case))
    return cases


def _derived_stated(texts: Sequence[str], language: Language, case: Any) -> int:
    values = [f.value for text in texts for f in figures(text, language)]
    derived = [e for e in getattr(case, "expected_numbers", []) if e.derived]
    return sum(any(single_harness._matches(v, e) for v in values) for e in derived)


async def run_single(case: Any, dataset: str, cassettes_dir: Path) -> EngineResult | None:
    path = single_cassette_path(case.id, cassettes_dir)
    if not path.is_file():
        return None
    cassette = read_single_cassette(path)
    router, replay_tools = replay_single(cassette)
    tools = single_harness._SeenTools(replay_tools)
    outcome = await run_agent(case.question, router, tools, Budget(), single_harness._noop)
    answer = outcome.answer
    result = EngineResult(outcome.status, outcome.usage["model_calls"])
    result.elapsed_ms = cassette.elapsed_ms
    result.figures_total, result.figures_supported = single_harness.coverage(answer, tools.seen)
    if isinstance(case, AdviceCase | InjectionCase):
        result.passed = single_harness.score_case(case, dataset, outcome, tools.seen).passed
        return result
    result.numbers_expected = len(case.expected_numbers)
    result.numbers_matched = single_harness.numbers_matched(answer, case)
    # The single agent has no calculation step, so no derived number it states is verified.
    result.derived_stated = _derived_stated(single_harness._texts(answer), answer.language, case)
    return result


async def run_graph(case: Any, dataset: str, cassettes_dir: Path) -> EngineResult | None:
    path = graph_cassette_path(case.id, cassettes_dir)
    if not path.is_file():
        return None
    cassette = read_graph_cassette(path)
    router, toolbox = replay_graph(cassette)
    outcome = await run_graph_case(case, router, toolbox, replay_sandbox(cassette))
    scored = graph_harness.score_case(case, dataset, outcome)
    result = EngineResult(
        scored.status,
        scored.model_calls,
        figures_total=scored.figures_total,
        figures_supported=scored.figures_supported,
        numbers_expected=scored.numbers_expected,
        numbers_matched=scored.numbers_matched,
        elapsed_ms=cassette.elapsed_ms,
    )
    if isinstance(case, AdviceCase | InjectionCase):
        result.passed = scored.passed
    elif isinstance(case, DeepCase):
        result.derived_stated = scored.derived_stated
        result.derived_verified = scored.derived_from_calculations
    return result


async def run_comparison(
    datasets_dir: Path = DATASETS_DIR,
    single_cassettes: Path = SINGLE_CASSETTES_DIR,
    graph_cassettes: Path = GRAPH_CASSETTES_DIR,
) -> Comparison:
    comparison = Comparison()
    for dataset, case in shared_cases(datasets_dir):
        row = CaseRow(case.id, dataset)
        for engine in ("single", "graph"):
            try:
                if engine == "single":
                    row.single = await run_single(case, dataset, single_cassettes)
                else:
                    row.graph = await run_graph(case, dataset, graph_cassettes)
            except (CassetteMiss, ToolCassetteMiss, SandboxCassetteMiss) as exc:
                comparison.stale.append(f"{engine} {dataset}/{case.id}: stale cassette ({exc})")
                row.notes.append(f"{engine}: stale cassette")
                continue
            if getattr(row, engine) is None:
                row.notes.append(f"{engine}: no cassette")
        comparison.rows.append(row)
    return comparison


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def engine_metrics(results: Sequence[tuple[str, EngineResult]]) -> dict[str, Any]:
    """Metrics for one engine over (dataset, result) pairs; None where nothing was measured."""

    def of(*datasets: str) -> list[EngineResult]:
        return [r for d, r in results if d in datasets]

    answers, deep = of("answers_golden"), of("deep_golden")
    advice, injection = of("advice_refusal"), of("injection")
    golden = answers + deep
    every = [r for _, r in results]
    timed = sorted(r.elapsed_ms for r in every if r.elapsed_ms is not None)
    return {
        "cases": len(every),
        "numeric_match_rate": _rate(
            sum(r.numbers_matched for r in answers), sum(r.numbers_expected for r in answers)
        ),
        "deep_numeric_match_rate": _rate(
            sum(r.numbers_matched for r in deep), sum(r.numbers_expected for r in deep)
        ),
        "citation_coverage": _rate(
            sum(r.figures_supported for r in every), sum(r.figures_total for r in every)
        ),
        "verified_number_rate": _rate(
            sum(r.derived_verified for r in golden), sum(r.derived_stated for r in golden)
        ),
        "refusal_rate": _rate(sum(bool(r.passed) for r in advice), len(advice)),
        "injection_pass_rate": _rate(sum(bool(r.passed) for r in injection), len(injection)),
        "partial_rate": _rate(sum(r.status == "partial" for r in golden), len(golden)),
        "model_calls_per_run": round(sum(r.model_calls for r in every) / len(every), 2)
        if every
        else None,
        "max_model_calls": max((r.model_calls for r in every), default=None),
        "latency_recorded": f"{len(timed)}/{len(every)}",
        "latency_p50_ms": percentile(timed, 50) if timed else None,
        "latency_p95_ms": percentile(timed, 95) if timed else None,
    }


def summarize(comparison: Comparison) -> dict[str, Any]:
    paired = [r for r in comparison.rows if r.paired]
    outcomes = [r.outcome() for r in paired]
    by_dataset: dict[str, dict[str, int]] = {}
    for row, outcome in zip(paired, outcomes, strict=True):
        counts = by_dataset.setdefault(row.dataset, {"win": 0, "loss": 0, "tie": 0})
        counts[str(outcome)] += 1
    return {
        "paired_cases": len(paired),
        "engines": {
            "single": engine_metrics([(r.dataset, r.single) for r in paired if r.single]),
            "graph": engine_metrics([(r.dataset, r.graph) for r in paired if r.graph]),
        },
        "graph_vs_single": {
            "win": outcomes.count("win"),
            "loss": outcomes.count("loss"),
            "tie": outcomes.count("tie"),
            "by_dataset": by_dataset,
        },
    }


def _cell(value: Any) -> str:
    return "n/a" if value is None else str(value)


def _engine_cell(result: Mapping[str, Any] | None) -> str:
    if result is None:
        return "—"
    if result["passed"] is not None:
        verdict = "pass" if result["passed"] else "fail"
    elif result["numbers_expected"]:
        verdict = f"{result['numbers_matched']}/{result['numbers_expected']}"
    else:
        verdict = "no numbers"
    return f"{result['status']}, {verdict}, {result['model_calls']} calls"


def report_markdown(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    single, graph = summary["engines"]["single"], summary["engines"]["graph"]
    lines = [
        f"# Single agent vs multi-agent graph, {report['date']}",
        "",
        f"Metrics cover the {summary['paired_cases']} cases both engines replayed. "
        "Rerun with `uv run python -m evals.run compare --replay`.",
        "",
        "| Metric | single | graph |",
        "|---|---|---|",
    ]
    lines += [f"| {name} | {_cell(single[name])} | {_cell(graph[name])} |" for name in single]
    record = summary["graph_vs_single"]
    lines += [
        "",
        f"Graph against single: {record['win']} wins, {record['loss']} losses, "
        f"{record['tie']} ties.",
        "",
        "| Dataset | Wins | Losses | Ties |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {name} | {c['win']} | {c['loss']} | {c['tie']} |"
        for name, c in sorted(record["by_dataset"].items())
    ]
    lines += [
        "",
        "## Cases",
        "",
        "| Case | Dataset | single | graph | Graph | Notes |",
        "|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {r['case_id']} | {r['dataset']} | {_engine_cell(r['single'])} | "
        f"{_engine_cell(r['graph'])} | {r['outcome'] or '—'} | {'; '.join(r['notes'])} |"
        for r in report["cases"]
    ]
    unpaired = [r for r in report["cases"] if r["outcome"] is None]
    if unpaired:
        lines += ["", "## Not compared", ""]
        lines += [f"- {r['dataset']}/{r['case_id']}: {'; '.join(r['notes'])}" for r in unpaired]
    if report["stale"]:
        lines += ["", "## Stale cassettes", "", *(f"- {s}" for s in report["stale"])]
    return "\n".join(lines) + "\n"


def build_report(comparison: Comparison, day: str) -> dict[str, Any]:
    return {
        "date": day,
        "summary": summarize(comparison),
        "cases": [
            {
                "case_id": r.case_id,
                "dataset": r.dataset,
                "single": asdict(r.single) if r.single else None,
                "graph": asdict(r.graph) if r.graph else None,
                "outcome": r.outcome(),
                "notes": r.notes,
            }
            for r in comparison.rows
        ],
        "stale": comparison.stale,
    }


def write_report(report: Mapping[str, Any], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stem = directory / f"{report['date']}-single-vs-multi"
    stem.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    stem.with_suffix(".md").write_text(report_markdown(report), encoding="utf-8")
    return stem.with_suffix(".md")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run compare", description=__doc__)
    parser.add_argument("--replay", action="store_true", required=True)
    parser.add_argument("--datasets", type=Path, default=DATASETS_DIR)
    parser.add_argument("--single-cassettes", type=Path, default=SINGLE_CASSETTES_DIR)
    parser.add_argument("--graph-cassettes", type=Path, default=GRAPH_CASSETTES_DIR)
    parser.add_argument("--reports", type=Path, default=REPORTS_DIR)
    args = parser.parse_args(argv)

    comparison = asyncio.run(
        run_comparison(args.datasets, args.single_cassettes, args.graph_cassettes)
    )
    report = build_report(comparison, datetime.now(UTC).date().isoformat())
    print(f"report: {write_report(report, args.reports)}")
    print(json.dumps(report["summary"], indent=2))
    for stale in comparison.stale:
        print(stale, file=sys.stderr)
    return 1 if comparison.stale else 0


if __name__ == "__main__":
    sys.exit(main())
