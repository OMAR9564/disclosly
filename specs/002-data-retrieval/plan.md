# Implementation Plan: Data + Retrieval

**Branch**: `002-data-retrieval` | **Date**: 2026-10-19 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-data-retrieval/spec.md`

## Summary

Build the corpus and the search layer that every later phase depends on. A background worker
fetches KAP and SEC EDGAR disclosures for 40 companies (top 20 BIST 30 by market cap + 20 large
US filers), parses them into sections and tables, chunks them section-aware, extracts financial
facts, and deletes raw files. The pipeline is idempotent by content hash. Bulk embedding runs on
the developer laptop against the server database; the server embeds only queries and small new
documents. Hybrid search combines pgvector cosine (HNSW) and Postgres full-text (Turkish and
English, accent-insensitive) with Reciprocal Rank Fusion and an optional local cross-encoder
rerank. A retrieval golden set (≥ 50 TR + EN questions) and an eval command report recall@5,
recall@10 and MRR per configuration; CI replays a deterministic subset from recorded vectors.
The chunk size / k / rerank choice goes into ADR 0002.

## Technical Context

**Language/Version**: Python 3.12+ (mypy strict, Pydantic v2)

**Primary Dependencies**: existing (SQLAlchemy 2, psycopg 3, Alembic, Redis, Pydantic). New,
approved by owner 2026-10-19 (see [research.md](research.md) §R10): `arq`, `httpx`, `pgvector`,
`sentence-transformers` (CPU torch, worker and laptop only), `pypdf`, `pytesseract` + system
`tesseract-ocr-tur`, `selectolax`.

**Storage**: PostgreSQL 16 + pgvector (HNSW, 384 dims) + `tsvector` with `unaccent`-based
Turkish and English configs; Redis for query-embedding cache and the arq queue.

**Testing**: pytest + pytest-asyncio; integration tests against Postgres/Redis service
containers; recorded HTTP fixtures for KAP/SEC; retrieval eval cassettes (pre-computed vectors
and rerank scores) for CI.

**Target Platform**: Linux VPS (Docker Compose) for worker/DB; developer laptop (Fedora) for
bulk embedding.

**Project Type**: Backend pipeline + library (search) + CLI commands. No new HTTP endpoints or
UI in this phase.

**Performance Goals**: search p95 < 1 s on the VPS (SC-005); bulk embedding of the full corpus
in one laptop session (target < 2 h).

**Constraints**: worker container 768 MB (`mem_limit`), Postgres 1 GB, corpus disk < 25 GB, no
raw files after parse, SEC ≤ 5 req/s with declared User-Agent, KAP ≤ 1 req/s, no live model or
network calls in CI, $0 budget.

**Scale/Scope**: 40 companies, 2 years; estimate ~1,500 documents, ~150k chunks, ~60k facts
(sizing in [research.md](research.md) §R9).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | How this plan complies |
|---|---|---|
| I. Spec-First | Pass | Spec approved, FR-002 resolved; plan traces to FR/SC IDs. |
| II. Test-First | Pass | Unit tests per parser/chunker/fusion; integration tests for idempotency and filters; retrieval eval cassette subset in CI; coverage gate extended to `ingestion`, `retrieval`, `workers`. |
| III. Simplicity | Pass | Reuses Postgres for vectors, FTS, facts and runs (no vector DB, no search engine). Each new dependency is justified in research §R10 and approved by the owner on 2026-10-19. ADR 0002 records retrieval choices. |
| IV. Security & No Secrets | Pass | SEC contact via env `SEC_USER_AGENT`; no secrets in fixtures; document text stored and returned as data only; cassettes contain public filings only. |
| V. Observability | Pass | Structured JSON logs per document and run; OTel spans around fetch/parse/embed/search (light exporter as in Phase 1); `ingestion_runs` table records counts and failures. |
| Quality gates | Pass | Ruff/mypy/coverage paths extended; eval gate gains a recall@10 tolerance check. |

Post-design re-check (after Phase 1): unchanged, all pass. Dependencies approved (R10).

## Project Structure

### Documentation (this feature)

```text
specs/002-data-retrieval/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── search.md          # search_documents / list_documents library contract
│   ├── cli.md             # ingestion, embedding and eval commands
│   └── golden-set.md      # retrieval_golden.jsonl + cassette format
└── tasks.md               # /speckit-tasks
```

### Source Code (repository root)

```text
ingestion/
├── config.py              # company list loader, rate limits, paths
├── companies.yaml         # 20 BIST + 20 US companies (tickers, KAP ids, CIKs)
├── schema.py              # SQLAlchemy tables (shared with retrieval)
├── sources/
│   ├── base.py            # Source protocol: list -> fetch
│   ├── sec.py             # EDGAR submissions + filings + companyfacts
│   └── kap.py             # KAP disclosures (new adapter, R2)
├── parse/
│   ├── html.py            # 10-K/10-Q items, KAP HTML
│   ├── pdf.py             # text PDFs + OCR fallback
│   └── tables.py
├── chunk.py               # section-aware, table-row-safe chunker
├── facts.py               # XBRL companyfacts + KAP statement mapping
├── pipeline.py            # fetch -> parse -> chunk -> facts -> cleanup, idempotent
├── embed.py               # batch embed (laptop) + small-batch embed (worker)
└── run.py                 # CLI entry points
retrieval/
├── embeddings.py          # query embedding + Redis cache
├── fulltext.py
├── vector.py
├── fusion.py              # RRF
├── rerank.py
└── search.py              # search_documents, list_documents
workers/
└── jobs.py                # arq: ingest_company, embed_pending
evals/
├── datasets/retrieval_golden.jsonl
├── cassettes/retrieval/   # fixture corpus + query vectors + rerank scores
├── retrieval.py           # recall@k, MRR, config sweep, report writer
└── reports/
api/migrations/versions/0002_corpus.py
infra/docker-compose.yml   # + worker service (768m)
docs/adr/0002-retrieval-configuration.md
tests/unit/{ingestion,retrieval}/  tests/integration/{ingestion,retrieval}/
```

**Structure Decision**: Follow PROJECT_SPEC §18 top-level packages (`ingestion/`,
`retrieval/`, `workers/`, `evals/`). Table definitions live in `ingestion/schema.py` and are
imported by `retrieval/`; migrations stay in `api/migrations/` with the existing Alembic setup.

## Complexity Tracking

No constitution violations. New dependencies approved in research §R10.
