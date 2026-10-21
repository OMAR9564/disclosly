import asyncio
from typing import Any

import pytest

from evals.latency import measure, percentile, summary
from retrieval.types import SearchFilters


def test_percentile_nearest_rank() -> None:
    values = [float(v) for v in range(1, 101)]
    assert percentile(values, 50) == 50.0
    assert percentile(values, 95) == 95.0
    assert percentile([7.0], 95) == 7.0


def test_percentile_rejects_empty() -> None:
    with pytest.raises(ValueError):
        percentile([], 95)


class FakeService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def search(self, query: str, filters: SearchFilters | None = None, k: int = 6) -> Any:
        self.calls.append(query)
        return []


def test_measure_runs_every_question_each_round() -> None:
    service = FakeService()
    questions = [("a", SearchFilters()), ("b", SearchFilters())]
    timings = asyncio.run(measure(service, questions, rounds=3))
    assert service.calls == ["a", "b"] * 3
    assert len(timings) == 6
    assert all(t >= 0 for t in timings)


def test_summary_fields() -> None:
    result = summary([10.0, 20.0, 30.0], rounds=1, rerank=True)
    assert result == {
        "searches": 3,
        "rounds": 1,
        "rerank": True,
        "p50_ms": 20.0,
        "p95_ms": 30.0,
        "max_ms": 30.0,
    }
