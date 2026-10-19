import pytest

from retrieval.fusion import RRF_K, rrf


def test_rrf_sums_reciprocal_ranks() -> None:
    fused = dict(rrf([["a", "b"], ["b", "c"]]))
    assert fused["b"] == pytest.approx(1 / (RRF_K + 2) + 1 / (RRF_K + 1))
    assert fused["a"] == pytest.approx(1 / (RRF_K + 1))
    assert fused["c"] == pytest.approx(1 / (RRF_K + 2))


def test_rrf_order_and_ties_by_id() -> None:
    assert [cid for cid, _ in rrf([["a", "b"], ["b", "c"]])] == ["b", "a", "c"]
    # "x" and "y" both rank first in one list: tie broken by ID.
    assert [cid for cid, _ in rrf([["y"], ["x"]])] == ["x", "y"]


def test_rrf_empty() -> None:
    assert rrf([[], []]) == []
    assert rrf([]) == []
