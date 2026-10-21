"""Optional cross-encoder rerank (FR-017; switch `RETRIEVAL_RERANK`)."""

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from ingestion.config import IngestionSettings


class Reranker(Protocol):
    def score(self, query: str, texts: Sequence[str]) -> list[float]: ...


class CrossEncoderReranker:
    """Local CPU model; needs the `ml` dependency group.

    `max_length` caps the tokens of each (query, passage) pair. `quantize` converts the linear
    layers to int8 (PyTorch dynamic quantization), which is faster on CPU.
    """

    def __init__(
        self, model_name: str, *, max_length: int | None = None, quantize: bool = False
    ) -> None:
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model_name, device="cpu", max_length=max_length)
        if quantize:
            import torch

            # cast: torch leaves this function untyped, and CI type-checks without torch.
            quantize_dynamic = cast(Callable[..., object], torch.ao.quantization.quantize_dynamic)
            quantize_dynamic(self._model, {torch.nn.Linear}, dtype=torch.qint8, inplace=True)

    @classmethod
    def from_settings(cls, settings: "IngestionSettings") -> "CrossEncoderReranker":
        return cls(
            settings.rerank_model,
            max_length=settings.retrieval_rerank_max_length,
            quantize=settings.retrieval_rerank_quantize,
        )

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
