"""Optional cross-encoder rerank (FR-017; switch `RETRIEVAL_RERANK`)."""

from collections.abc import Sequence
from typing import Protocol


class Reranker(Protocol):
    def score(self, query: str, texts: Sequence[str]) -> list[float]: ...


class CrossEncoderReranker:
    """Local CPU model; needs the `ml` dependency group."""

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model_name, device="cpu")

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        scores = self._model.predict([(query, t) for t in texts], convert_to_numpy=True)
        return [float(s) for s in scores]


def rerank(
    reranker: Reranker, query: str, candidates: Sequence[tuple[str, str]], top: int
) -> list[tuple[str, float]]:
    """Score the first `top` (id, text) candidates; best first, ties by ID. The rest is dropped."""
    selected = list(candidates[:top])
    if not selected:
        return []
    scores = reranker.score(query, [t for _, t in selected])
    if len(scores) != len(selected):
        raise ValueError(f"reranker must return {len(selected)} scores")
    return sorted(
        ((cid, s) for (cid, _), s in zip(selected, scores, strict=True)),
        key=lambda pair: (-pair[1], pair[0]),
    )
