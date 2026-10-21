"""Cassette-only eval runner: `python -m evals.run --cassettes-only`.

Discovers every case in `evals/datasets/*.jsonl` (one JSON object per line with a string `id`)
and requires a recorded response at `evals/cassettes/<dataset>/<id>.json`. It never makes
network calls. With no LLM features yet there is nothing to score, so a case passes when its
cassette exists; scoring is added with the first real eval.

`retrieval_golden.jsonl` has its own runner and cassette layout (`python -m evals.retrieval`),
and the agent datasets have theirs (`python -m evals.run agent --replay`, evals/agent.py), so
this runner skips them.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

DATASETS_DIR = Path(__file__).parent / "datasets"
CASSETTES_DIR = Path(__file__).parent / "cassettes"
# Datasets scored by their own runner (evals/retrieval.py, evals/agent.py).
OWN_RUNNER = frozenset({"retrieval_golden", "answers_golden", "advice_refusal", "injection"})


@dataclass
class Report:
    cases: int = 0
    failures: list[str] = field(default_factory=list)


def run_suite(datasets_dir: Path, cassettes_dir: Path) -> Report:
    report = Report()
    for dataset in sorted(datasets_dir.glob("*.jsonl")):
        if dataset.stem in OWN_RUNNER:
            continue
        lines = dataset.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            report.cases += 1
            where = f"{dataset.name}:{number}"
            try:
                case = json.loads(line)
            except json.JSONDecodeError:
                report.failures.append(f"{where}: invalid JSON")
                continue
            case_id = case.get("id") if isinstance(case, dict) else None
            if not isinstance(case_id, str) or not case_id:
                report.failures.append(f"{where}: case has no string 'id'")
                continue
            if not (cassettes_dir / dataset.stem / f"{case_id}.json").is_file():
                report.failures.append(f"{dataset.stem}/{case_id}: no recorded cassette")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list[:1] == ["agent"]:
        from evals.agent import main as agent_main

        return agent_main(args_list[1:])
    parser = argparse.ArgumentParser(prog="python -m evals.run", description=__doc__)
    parser.add_argument(
        "--cassettes-only",
        action="store_true",
        required=True,
        help="replay recorded responses only (the only supported mode)",
    )
    parser.add_argument("--datasets", type=Path, default=DATASETS_DIR)
    parser.add_argument("--cassettes", type=Path, default=CASSETTES_DIR)
    args = parser.parse_args(args_list)

    report = run_suite(args.datasets, args.cassettes)
    for failure in report.failures:
        print(failure, file=sys.stderr)
    if report.failures:
        print(f"{report.cases} cases, {len(report.failures)} failed")
        return 1
    print(f"{report.cases} cases, passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
