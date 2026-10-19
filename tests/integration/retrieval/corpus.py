"""A small hand-made TR + EN corpus with fixed vectors (no model)."""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, insert

from ingestion import schema
from ingestion.schema import EMBEDDING_DIM

VERSION = "v1-600-64"


def basis(i: int) -> list[float]:
    """Unit vector on axis `i`: chunks on the same axis are nearest neighbours."""
    vector = [0.0] * EMBEDDING_DIM
    vector[i] = 1.0
    return vector


def chunk_id(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


@dataclass(frozen=True)
class Doc:
    id: int
    company: int
    doc_type: str
    period: str
    language: str
    supersedes: int | None = None


@dataclass(frozen=True)
class Chunk:
    name: str
    doc: int
    section: str
    text: str
    axis: int | None
    kind: str = "text"
    page: int | None = 3
    version: str = VERSION


COMPANIES = [
    {"id": 1, "ticker": "THYAO", "name": "Türk Hava Yolları", "market": "bist",
     "kap_member_id": "m1", "sec_cik": None},
    {"id": 2, "ticker": "AAPL", "name": "Apple Inc.", "market": "us",
     "kap_member_id": None, "sec_cik": "0000320193"},
]  # fmt: skip
DOCS = [
    Doc(10, 1, "kap_financial_report", "2025-Q3", "tr"),
    Doc(11, 1, "kap_material_event", "2024-Q2", "tr"),
    Doc(12, 1, "kap_annual_report", "2025-FY", "tr"),
    Doc(20, 2, "10-K", "2023-FY", "en"),
    Doc(21, 2, "10-K", "2024-FY", "en", supersedes=20),
    Doc(22, 2, "10-Q", "2025-Q1", "en"),
]
CHUNKS = [
    Chunk("thy-debt", 10, "body", "Net borç 2025 yılının üçüncü çeyreğinde azaldı.", 0),
    Chunk("thy-favok", 10, "body", "FAVÖK marjı yüzde 25 seviyesinde gerçekleşti.", 1, "table"),
    Chunk("thy-order", 11, "body", "Şirket yeni geniş gövdeli uçak siparişi verdi.", 2, page=None),
    Chunk("thy-annual", 12, "Yönetim", "Yıllık faaliyet raporunda filo büyümesi anlatıldı.", 4),
    Chunk("aapl-old", 20, "Item 7", "Obsolete revenue discussion and net debt.", 0),
    Chunk("aapl-debt", 21, "Item 7", "Net debt decreased while revenue grew.", 0),
    Chunk("aapl-risk", 21, "Item 1A", "Supply chain risk factors remain.", 3),
    Chunk("aapl-q", 22, "Item 2", "Quarterly revenue and services growth.", 5),
    Chunk("aapl-raw", 22, "Item 2", "Revenue without an embedding yet.", None),
    Chunk("thy-v2", 10, "body", "Net borç farklı parçalama sürümü.", 0, version="v1-400-64"),
]


@dataclass(frozen=True)
class Corpus:
    engine: Engine
    ids: dict[str, str]

    def names(self, chunk_ids: Sequence[str]) -> list[str]:
        reverse = {v: k for k, v in self.ids.items()}
        return [reverse[c] for c in chunk_ids]


def load(engine: Engine) -> Corpus:
    sections: dict[tuple[int, str], int] = {}
    for c in CHUNKS:
        sections.setdefault((c.doc, c.section), 100 + len(sections))
    docs = {d.id: d for d in DOCS}
    tickers = {c["id"]: c["ticker"] for c in COMPANIES}
    with engine.begin() as conn:
        conn.execute(insert(schema.companies), COMPANIES)
        conn.execute(
            insert(schema.documents),
            [
                {
                    "id": d.id,
                    "company_id": d.company,
                    "source": "kap" if d.language == "tr" else "sec",
                    "doc_type": d.doc_type,
                    "period": d.period,
                    "published_at": datetime(2025, 1, 1, tzinfo=UTC),
                    "source_url": f"https://example.test/doc/{d.id}",
                    "content_hash": chunk_id(f"doc{d.id}"),
                    "language": d.language,
                    "status": "embedded",
                    "supersedes_id": d.supersedes,
                }
                for d in DOCS
            ],
        )
        conn.execute(
            insert(schema.sections),
            [
                {"id": sid, "document_id": doc, "ordinal": sid, "label": label}
                for (doc, label), sid in sections.items()
            ],
        )
        rows = []
        for ordinal, c in enumerate(CHUNKS):
            d = docs[c.doc]
            rows.append(
                {
                    "id": chunk_id(c.name),
                    "document_id": c.doc,
                    "section_id": sections[(c.doc, c.section)],
                    "chunker_version": c.version,
                    "ordinal": ordinal,
                    "kind": c.kind,
                    "text": c.text,
                    "token_count": len(c.text.split()),
                    "page": c.page,
                    "company_id": d.company,
                    "ticker": tickers[d.company],
                    "period": d.period,
                    "doc_type": d.doc_type,
                    "language": d.language,
                    "embedding": None if c.axis is None else basis(c.axis),
                }
            )
        conn.execute(insert(schema.chunks), rows)
    return Corpus(engine, {c.name: chunk_id(c.name) for c in CHUNKS})


@pytest.fixture
def corpus(engine: Engine) -> Corpus:
    return load(engine)
