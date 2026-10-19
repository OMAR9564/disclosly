from collections.abc import Sequence

import pytest

from retrieval.rerank import rerank


class LengthReranker:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        self.seen.extend(texts)
        return [float(len(t)) for t in texts]


def test_rerank_scores_top_and_sorts() -> None:
    fake = LengthReranker()
    candidates = [("a", "x"), ("b", "xxx"), ("c", "xx"), ("d", "xxxxxxx")]
    result = rerank(fake, "q", candidates, top=3)
    assert result == [("b", 3.0), ("c", 2.0), ("a", 1.0)]
    assert fake.seen == ["x", "xxx", "xx"]  # "d" is outside the top 3


def test_rerank_ties_by_id_and_empty() -> None:
    assert [cid for cid, _ in rerank(LengthReranker(), "q", [("b", "x"), ("a", "y")], 5)] == [
        "a",
        "b",
    ]
    assert rerank(LengthReranker(), "q", [], 5) == []


def test_rerank_rejects_wrong_score_count() -> None:
    class Broken:
        def score(self, query: str, texts: Sequence[str]) -> list[float]:
            return []

    with pytest.raises(ValueError, match="scores"):
        rerank(Broken(), "q", [("a", "x")], 1)
