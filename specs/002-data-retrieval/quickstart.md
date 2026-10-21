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

## Results (T033, 2026-10-20)

Run against the production corpus on the server. The steps that write data (1 and 3) were not
run again for this record; their evidence comes from the runs and tests named below.

| Step | Result | Evidence |
|---|---|---|
| 1. Idempotent ingestion (SC-002) | Pass | `tests/integration/ingestion/test_pipeline.py` (second run: 0 new, 0 changed). On the server, manual run 46 over all tickers skipped 1,137 known documents and fetched 5 new filings. `/data/ingest` in the worker is empty |
| 2. Metadata and facts (SC-003) | Pass | `status --json`: 2,465 embedded, 10 failed, 89,351 chunks, 0 chunks missing metadata, 0 facts missing source, 0 chunks without embedding, `disk_bytes` 696 MB |
| 3. Embed and search (US2) | Pass | Bulk embedding on the laptop (runbook, "Corpus ingestion"); `tests/integration/retrieval/test_search.py` covers the US2 scenarios |
| 4. Retrieval eval (SC-004, SC-007) | Measured, target not met (accepted) | `evals/reports/retrieval-2026-10-20-rerank-64-int8.md`: recall@10 0.779 (tr 0.824, en 0.735), target 0.80; ADR 0002 cites the numbers |
| 5. CI subset | Pass | CI eval job, `--cassettes-only` against `evals/baselines/retrieval.json` |
| 6. Latency (SC-005) | Pass | `python -m evals.latency --rounds 3`: p50 614 ms, p95 878 ms, max 1,114 ms (runbook, "Search latency") |

### PROJECT_SPEC §20, phase 2

| Acceptance criterion | Status |
|---|---|
| Idempotent pipeline | Met (step 1) |
| recall@10 measured and reported | Met (step 4; the 0.80 target in SC-004 is not reached, accepted by the owner on 2026-10-20) |
| Chunk/rerank decision in ADR | Met (`docs/adr/0002-retrieval-configuration.md`) |

Open items carried to later phases: SEC chunk-size sweep, English full-text matching, re-recording
the rerank cassettes with the new reranker setting, and memory for the process that serves search
(about 1.4 GiB with both models).
