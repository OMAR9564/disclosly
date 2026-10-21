# Contract: Search library (`retrieval.search`)

Internal Python contract. Phase 3 wraps it as the `search_documents` / `list_documents` tools
(PROJECT_SPEC §7); no HTTP endpoint in this phase.

```python
class SearchFilters(BaseModel):
    tickers: list[str] | None = None
    period_from: str | None = None      # "2024-Q1" / "2024-FY", inclusive
    period_to: str | None = None
    doc_types: list[DocType] | None = None
    sections: list[str] | None = None
    language: Literal["tr", "en"] | None = None

class SourceRef(BaseModel):
    document_id: int
    ticker: str
    doc_type: DocType
    period: str
    section: str
    page: int | None
    source_url: str

class SearchHit(BaseModel):
    chunk_id: str
    text: str                           # untrusted document text
    kind: Literal["text", "table"]
    source: SourceRef
    score: float                        # final score (rerank or RRF)
    vector_rank: int | None
    fulltext_rank: int | None

async def search_documents(
    query: str, filters: SearchFilters | None = None, k: int = 6
) -> list[SearchHit]: ...

async def list_documents(
    ticker: str, doc_types: list[DocType] | None = None,
    period_from: str | None = None, period_to: str | None = None,
) -> list[DocumentMeta]: ...
```

Rules:
- `1 ≤ k ≤ 50`; empty or whitespace query raises `ValueError`.
- No hit may violate the filters (FR-015).
- No matches returns `[]`, never an error.
- Hits are sorted by `score` descending; ties broken by `chunk_id`.
- Configuration (env): `RETRIEVAL_CHUNKER_VERSION`, `RETRIEVAL_RERANK` (bool),
  `RETRIEVAL_CANDIDATES` (default 50), `RETRIEVAL_RERANK_TOP` (default 20),
  `RETRIEVAL_RERANK_MAX_LENGTH` (tokens per query-passage pair, default 64),
  `RETRIEVAL_RERANK_QUANTIZE` (int8 reranker weights, default true).
