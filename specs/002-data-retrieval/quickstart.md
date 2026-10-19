# Quickstart: Data + Retrieval validation

## Prerequisites

- Phase 1 stack running locally (`make up`), migrations applied (includes `0002_corpus`).
- `.env` has `SEC_USER_AGENT` set to a real contact string.
- Laptop: `uv sync --group ml` for embedding.

## 1. Idempotent ingestion (US1, SC-002)

```bash
uv run python -m ingestion.run ingest --tickers AAPL,THYAO
```
```bash
uv run python -m ingestion.run ingest --tickers AAPL,THYAO
```

Expected: second summary shows `"fetched": 0` and `skipped` equal to the first run's total;
`status --json` counts unchanged; no files left under `INGEST_DATA_DIR`.

## 2. Metadata and facts (US1, SC-003)

```bash
uv run python -m ingestion.run status --json
```

Expected: 0 chunks with missing ticker/period/doc_type/section/language; 0 facts without
`source_document_id`; integration test `tests/integration/ingestion/test_pipeline.py` passes.

## 3. Embed and search (US2)

```bash
uv run python -m ingestion.embed --limit 2000
```

Then from a Python shell: `await search_documents("THYAO net borç 2025",
SearchFilters(tickers=["THYAO"]))` returns ≤ 6 THYAO hits with source refs; a filter with no
match returns `[]`. See [contracts/search.md](contracts/search.md).

## 4. Retrieval eval (US3, SC-004, SC-007)

```bash
uv run python -m evals.retrieval --configs v1-400-64,v1-600-64,v1-800-64 --rerank on,off
```

Expected: `evals/reports/retrieval-<date>.md` with recall@5/@10/MRR per config and language;
`docs/adr/0002-retrieval-configuration.md` cites these numbers.

## 5. CI subset

```bash
uv run python -m evals.retrieval --cassettes-only
```

Expected: runs with network disabled; fails if recall@10 < baseline − 0.02.

## 6. Latency (SC-005)

Run the golden questions 3× on the VPS; p95 search latency from logs < 1 s.
