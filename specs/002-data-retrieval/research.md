# Research: Data + Retrieval

Each item lists Decision, Rationale, Alternatives considered.

## R1. SEC EDGAR access

- **Decision**: Use `data.sec.gov/submissions/CIK##########.json` to list 10-K/10-Q filings of
  the last 2 years, fetch the primary HTML document from the Archives path, and use
  `data.sec.gov/api/xbrl/companyfacts/CIK##########.json` for financial facts. Send
  `User-Agent: Disclosly <contact email>` from env `SEC_USER_AGENT`. Throttle to 5 req/s (SEC
  limit is 10) with backoff on 403/429/5xx.
- **Rationale**: Official, free, structured; companyfacts gives tagged numbers with accession
  numbers, so every fact has a source.
- **Alternatives**: Full-text search API (not needed), third-party SEC APIs (paid/limits),
  parsing XBRL instance files directly (more work than companyfacts).

## R2. KAP access

- **Decision**: Write a new KAP fetcher in `ingestion/sources/kap.py` behind the same `Source`
  protocol as SEC; the Numiver pipeline is not reused (owner decision, 2026-10-19). List
  disclosures per company through the public KAP web endpoints, limit to 1 req/s, cache
  listings per run, and scope to financial statements, material event disclosures and
  annual/interim reports for the 20 BIST companies. Fund reports are out of scope (FR-002);
  prospectuses are supported by the adapter but not required for SC-001.
- **Rationale**: Owner chose not to depend on Numiver code; KAP has no documented public API,
  so the adapter is isolated and tested against recorded responses.
- **Alternatives**: Porting the Numiver fetcher (rejected by owner); XBRL-only (KAP XBRL
  coverage and access are uneven).
- **Risk**: KAP endpoints are undocumented and may change; the adapter fails per document
  (FR-011) and SEC work is ordered first so the phase is not blocked.

## R3. Parsing and sections

- **Decision**: HTML via `selectolax`; 10-K/10-Q items detected by "Item N." heading patterns
  on the text stream. PDFs via `pypdf`; a page with < 50 extracted characters goes to
  Tesseract OCR (`tur+eng`). Tables extracted as header + rows; KAP sections from report
  headings. Unparseable section structure falls back to section `"body"`.
- **Rationale**: Small, permissively licensed, low-memory libraries.
- **Alternatives**: PyMuPDF (AGPL), unstructured/docling (heavy, hundreds of MB, exceeds the
  768 MB worker budget), pdfplumber (slower; may be added later for hard tables).

## R4. Chunking

- **Decision**: Count tokens with the embedding model's tokenizer. Pack paragraphs inside one
  section up to the target size (configurable; sweep 400 / 600 / 800, overlap 64). Tables are
  separate chunks split only at row boundaries, header repeated. Chunk ID =
  hash(document_id, chunker_version, ordinal).
- **Rationale**: Matches FR-007; tokenizer match avoids truncation at embed time (e5-small max
  512 tokens, so 800 needs checking: chunks above 512 tokens are truncated by the model, which
  the sweep will measure).
- **Alternatives**: Fixed character windows (break tables), semantic chunking (needs model
  calls).

## R5. Embedding and reranking models

- **Decision**: `intfloat/multilingual-e5-small` (384 dims, MIT) with `query:` / `passage:`
  prefixes. Reranker candidate `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (multilingual,
  Apache-2.0). Both on CPU through sentence-transformers, loaded once per worker.
- **Rationale**: Fixed by PROJECT_SPEC §4; the mMARCO cross-encoder is trained on translated
  MS MARCO, which covers Turkish.
- **Alternatives**: English MiniLM cross-encoder (weak on Turkish), bge-m3 (1024 dims, too
  large for 768 MB), API embeddings (violates §8.1).
- **Update (2026-10-20, T031)**: with the full 512-token window the reranker made p95 search
  latency 8.3 s on the 2 vCPU server (SC-005: < 1 s). The reranker now reads 64 tokens per pair
  and runs with int8 weights (`RETRIEVAL_RERANK_MAX_LENGTH`, `RETRIEVAL_RERANK_QUANTIZE`):
  p95 0.88 s, recall@10 0.779. Measurements in ADR 0002.

## R6. Hybrid search

- **Decision**: Two SQL queries with the same metadata `WHERE` clause: (a) HNSW cosine top 50
  (`vector_cosine_ops`, m=16, ef_construction=64, `hnsw.ef_search=80`), (b) `ts_rank_cd` top 50
  on a `tsvector` built per chunk language with configs `tr_unaccent` / `en_unaccent`
  (`unaccent` + snowball). The query is parsed with both configs. Fuse with RRF (k=60), rerank
  top 20 when enabled, return top k (default 6).
- **Rationale**: Filters before ranking (FR-015); `unaccent` handles "favok" vs "FAVÖK".
  Keeping both paths in Postgres avoids new infra.
- **Alternatives**: Elasticsearch/OpenSearch (RAM), pg_bm25/ParadeDB (extension not in the
  pgvector image).
- **Note**: HNSW with a selective filter can return fewer than 50 rows; enable pgvector
  iterative scans (`hnsw.iterative_scan = relaxed_order`, pgvector ≥ 0.8).

## R7. Query embedding cache

- **Decision**: Redis key `qemb:{model}:{sha256(normalised query)}`, float32 bytes, TTL 7 days.
- **Rationale**: FR-016; 1.5 KB per entry fits the 128 MB LRU cache.

## R8. Golden set and CI eval

- **Decision**: Expected evidence is stored as (document natural key, page or section,
  verbatim quote), not as chunk IDs. A retrieved chunk is a hit when it belongs to that
  document and contains the normalised quote. Metrics: recall@5, recall@10, MRR, per language.
  CI replays a fixture corpus (~10 documents, their chunks and vectors) plus recorded query
  vectors and rerank scores from `evals/cassettes/retrieval/`, loaded into the Postgres service
  container, so no model or network call happens. Gate: recall@10 must not drop more than
  0.02 below the baseline stored in `evals/baselines/retrieval.json` on `main`.
- **Rationale**: Quote-based labels survive chunk-size changes, so one golden set can compare
  configurations. Cassettes satisfy constitution II.
- **Alternatives**: Chunk-ID labels (invalid after re-chunking), loading the model in CI
  (download = network, slow).

## R9. Sizing

- **Decision/estimate**: ~1,500 documents, ~150k chunks; vectors 150k × 1.5 KB ≈ 230 MB plus
  HNSW ≈ 300 MB; text + tsvector ≈ 1 GB; facts small. Total well under 25 GB. The disk guard
  refuses new fetches above 20 GB used by the corpus database.
- **Rationale**: Fits Postgres 1 GB RAM with index partly cached; measured numbers go into the
  ADR.

## R10. New dependencies (approved by owner 2026-10-19)

| Package | Where | Why | Licence |
|---|---|---|---|
| `arq` | worker | Background jobs, fixed in PROJECT_SPEC §4 | MIT |
| `httpx` | worker | Async HTTP with timeouts for KAP/SEC | BSD |
| `pgvector` | api, worker | SQLAlchemy/psycopg vector type | MIT |
| `sentence-transformers` (+ CPU `torch`) | worker, laptop (`ml` group) | Local embed + rerank (§4) | Apache-2.0 / BSD |
| `pypdf` | worker | PDF text | BSD |
| `pytesseract` + `tesseract-ocr-tur` | worker image | Scanned KAP PDFs | Apache-2.0 |
| `selectolax` | worker | Fast HTML parsing | MIT |
| `PyYAML` | worker | `companies.yaml` | MIT |

The api image does not install the `ml` group, so its 512 MB limit is unaffected. New infra
component: the `worker` container (listed in PROJECT_SPEC §16.1, 768 MB).

## R11. Company list

- **Decision**: BIST: top 20 BIST 30 constituents by market cap at ingestion time, frozen in
  `companies.yaml` with the snapshot date. US: 20 large S&P 500 filers, approved by owner 2026-10-19 ( AAPL, MSFT, NVDA, AMZN, GOOGL, META, BRK-B, JPM, V, JNJ, WMT, PG, XOM,
  UNH, HD, KO, PEP, COST, ORCL, CVX).
- **Rationale**: FR-002 answer; a frozen list keeps runs reproducible.
