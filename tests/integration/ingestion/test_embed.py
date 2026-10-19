"""Embedding against a real Postgres with a deterministic fake embedder (no model)."""

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select, text

from ingestion import embed, schema
from ingestion.chunk import ChunkerConfig, WhitespaceCounter
from ingestion.embed import embed_pending
from ingestion.pipeline import Pipeline, SourceAdapter
from ingestion.schema import EMBEDDING_DIM
from tests.integration.ingestion.fakes import AAPL, FIXTURES, SEC_URL, FakeSource, sec_remote

pytestmark = pytest.mark.integration


class FakeEmbedder:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(len(texts))
        out = []
        for t in texts:
            seed = hashlib.sha256(t.encode()).digest()
            out.append([seed[i % 32] / 255 for i in range(EMBEDDING_DIM)])
        return out


async def _ingest(engine: Engine, tmp_path: Path) -> int:
    body = (FIXTURES / "sec" / "aapl-10k-trimmed.htm").read_bytes()
    pipeline = Pipeline(
        engine,
        [
            SourceAdapter(
                "sec", FakeSource("AAPL", [sec_remote()], {SEC_URL: body}), frozenset({"us"})
            )
        ],
        data_dir=tmp_path,
        disk_limit_gb=1,
        chunker=ChunkerConfig(size=20, overlap=4),
        counter=WhitespaceCounter(),
        size_bytes=lambda: 0,
    )
    await pipeline.run([AAPL], datetime(2024, 1, 1, tzinfo=UTC))
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(schema.chunks)).scalar_one())


def _status(engine: Engine) -> str:
    with engine.connect() as conn:
        return str(conn.execute(select(schema.documents.c.status)).scalar_one())


async def test_embeds_in_batches_and_marks_document(engine: Engine, tmp_path: Path) -> None:
    total = await _ingest(engine, tmp_path)
    assert total > 3
    fake = FakeEmbedder()

    first = embed_pending(engine, fake, batch=2, limit=3)
    assert first == 3 and fake.calls == [2, 1]
    assert _status(engine) == "chunked"  # not all chunks have vectors yet

    rest = embed_pending(engine, fake, batch=50, chunker_version="v1-20-4")
    assert rest == total - 3
    assert _status(engine) == "embedded"
    assert embed_pending(engine, fake) == 0
    with engine.connect() as conn:
        dims = conn.execute(text("SELECT DISTINCT vector_dims(embedding) FROM chunks")).scalars()
        assert list(dims) == [EMBEDDING_DIM]


async def test_other_chunker_version_is_left_alone(engine: Engine, tmp_path: Path) -> None:
    await _ingest(engine, tmp_path)
    assert embed_pending(engine, FakeEmbedder(), chunker_version="v1-400-64") == 0
    assert _status(engine) == "chunked"


async def test_wrong_vector_size_is_rejected(engine: Engine, tmp_path: Path) -> None:
    await _ingest(engine, tmp_path)

    class Short:
        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            return [[0.0] for _ in texts]

    with pytest.raises(ValueError, match="384"):
        embed_pending(engine, Short())
    with pytest.raises(ValueError, match="batch"):
        embed_pending(engine, FakeEmbedder(), batch=0)


async def test_cli_main(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await _ingest(engine, tmp_path)
    monkeypatch.setattr(embed, "SentenceTransformerEmbedder", lambda name: FakeEmbedder())
    assert embed.main(["--limit", "2", "--batch", "1"]) == 0
    assert capsys.readouterr().out.strip() == '{"embedded": 2}'

    def broken(name: str) -> None:
        raise ImportError("sentence_transformers")

    monkeypatch.setattr(embed, "SentenceTransformerEmbedder", broken)
    assert embed.main([]) == 1
