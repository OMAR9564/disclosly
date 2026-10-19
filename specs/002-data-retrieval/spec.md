# Feature Specification: Data + Retrieval

**Feature Branch**: `002-data-retrieval`

**Created**: 2026-10-19

**Status**: Draft

**Input**: User description: "PROJECT_SPEC.md Phase 2 (Section 20): KAP + SEC ingestion for ~40 companies; hybrid search; retrieval golden set. Acceptance: idempotent pipeline; recall@10 measured and reported; chunk/rerank decision in ADR."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ingest company disclosures into a searchable corpus (Priority: P1)

The owner runs the ingestion pipeline for a configured list of companies (about 20 BIST companies from KAP and about 20 US companies from SEC EDGAR). The pipeline fetches each company's disclosures, turns them into section-aware text and tables, splits them into chunks with full source metadata, extracts key financial line items, and removes raw files once parsed. Running it again changes nothing that is already stored.

**Why this priority**: Every later phase (agents, cited answers, MCP tools) depends on a corpus where each piece of text and each number traces back to a document, period and page or section. Without it nothing else can be built or measured.

**Independent Test**: Run ingestion for a small company list, check that documents, chunks and financial facts exist with complete metadata, then run it a second time and check that no duplicates were created and no stored record changed.

**Acceptance Scenarios**:

1. **Given** an empty corpus and a configured company list, **When** the owner runs ingestion, **Then** each company's in-scope documents are stored with source URL, company, ticker, period, document type, publication date and content hash.
2. **Given** a stored document, **When** ingestion runs again and the source content is unchanged, **Then** the document is skipped and the corpus is unchanged.
3. **Given** a parsed document that contains tables, **When** it is chunked, **Then** no table row is split across two chunks and every chunk carries company, ticker, period, document type, section, page (where applicable) and language.
4. **Given** a 10-K, 10-Q or KAP financial report, **When** ingestion finishes, **Then** core financial line items (revenue, net income, total debt, EBITDA or its inputs, and similar) are stored as structured facts, each pointing to its source document and location.
5. **Given** a document was parsed successfully, **When** ingestion finishes, **Then** the raw downloaded file is deleted while text, tables and vectors are kept.

---

### User Story 2 - Search the corpus with hybrid retrieval (Priority: P2)

A developer (and later the agent) sends a question in Turkish or English with optional filters (ticker, period range, document type, section, language) and receives the most relevant chunks, each with its source reference and score.

**Why this priority**: Retrieval quality decides whether later answers can be cited correctly. It needs the corpus from Story 1 but delivers value on its own as a searchable disclosure index.

**Independent Test**: Run a set of Turkish and English queries with and without filters against an ingested corpus and check that results respect filters, carry source references and come back within the latency target.

**Acceptance Scenarios**:

1. **Given** an ingested corpus, **When** a user searches "THYAO net borç 2025" with ticker filter THYAO, **Then** only THYAO chunks are returned, ranked by relevance, each with document, period, section and page.
2. **Given** a query whose wording does not appear literally in any chunk, **When** the user searches, **Then** semantically related chunks are still returned (meaning-based matching), and exact-term matches are also returned (keyword matching), merged into one ranked list.
3. **Given** a requested result count k, **When** the user searches, **Then** at most k results are returned (default 6).
4. **Given** filters that match no documents, **When** the user searches, **Then** an empty result is returned with no error.

---

### User Story 3 - Measure retrieval quality and record the tuning decision (Priority: P3)

The owner maintains a versioned golden set of at least 50 questions (Turkish and English) with their expected source chunks, runs a retrieval evaluation that reports recall@5, recall@10 and MRR, compares configurations (chunk size, k, reranker on/off), and records the chosen configuration and its evidence in an architecture decision record.

**Why this priority**: This is the phase acceptance gate. It needs Stories 1 and 2 to exist, and it turns tuning from guesswork into a recorded, repeatable decision.

**Independent Test**: Run the evaluation against the golden set and confirm a report with recall@5, recall@10 and MRR is produced per configuration, and that the ADR cites those numbers.

**Acceptance Scenarios**:

1. **Given** the golden set and an ingested corpus, **When** the owner runs the retrieval evaluation, **Then** a dated report with recall@5, recall@10 and MRR, split by language, is written to the evaluation reports area.
2. **Given** at least two chunk-size settings and reranker on/off, **When** the comparison runs, **Then** the report shows metrics per configuration side by side.
3. **Given** the comparison results, **When** the decision is made, **Then** an ADR records the chosen chunk size, k and reranker setting with the measured numbers that justify it, including whether the reranker helps or hurts Turkish queries.
4. **Given** a pull request, **When** CI runs, **Then** a deterministic retrieval evaluation subset runs without any live network or model calls and fails the build if recall@10 drops beyond the configured tolerance against the `main` baseline.

---

### Edge Cases

- A source is unreachable, rate-limits the request, or returns an error: the pipeline backs off, records the failure for that document, continues with the others, and a later run retries it.
- A document's content changes at the same URL (e.g. a corrected filing): it is treated as a new version by content hash; the previous version is not silently overwritten.
- A KAP PDF is scanned (image-only): text is obtained through OCR; if OCR fails the document is marked as failed, not stored empty.
- A document cannot be parsed into sections: it is still chunked with a generic section label rather than dropped, and the case is logged.
- A single table is larger than the chunk size: the table is split only at row boundaries, with header context repeated.
- Ingestion is interrupted midway: rerunning completes the remaining work without duplicating finished documents.
- Disk usage approaches the budget: the pipeline refuses new fetches and reports it rather than filling the disk.
- A query mixes Turkish and English, or contains Turkish characters typed without diacritics (e.g. "favok" for "FAVÖK"): keyword matching still finds the relevant chunks.
- Retrieved document text contains instruction-like content: it is stored and returned as plain data, never interpreted.

## Requirements *(mandatory)*

### Functional Requirements

**Ingestion**

- **FR-001**: The system MUST ingest disclosures from KAP (financial statements, material event disclosures, fund reports, IPO prospectuses) and SEC EDGAR (10-K and 10-Q for the last 2 years, plus structured company facts) for a configurable company list.
- **FR-002**: The initial company list MUST contain about 20 BIST companies and about 20 US companies. The BIST set is the top 20 BIST 30 constituents by market capitalisation at ingestion time; no funds are included in this phase.
- **FR-003**: Fetching MUST respect each source's terms of use and rate limits; SEC requests MUST identify the requester as SEC fair-access rules require.
- **FR-004**: For every fetched document the system MUST record source URL, company, ticker, period, document type, publication date and content hash.
- **FR-005**: The pipeline MUST be idempotent: a document whose content hash is already stored MUST be skipped, and repeated runs over unchanged sources MUST leave the corpus identical.
- **FR-006**: Parsing MUST preserve document section structure (e.g. 10-K Item 7 and Item 8; KAP report sections) and MUST extract tables as structured rows.
- **FR-007**: Chunking MUST be section-aware, target roughly 500–800 tokens with a small overlap, never split a table row, and attach company, ticker, period, document type, section, page and language to each chunk.
- **FR-008**: Each chunk MUST have a vector representation produced by a locally run embedding model; bulk document embedding MUST be possible on the developer laptop with the vectors uploaded to the server database.
- **FR-009**: The system MUST store core financial line items as structured facts, each with metric, period, value, unit, currency and a reference to the source document and location.
- **FR-010**: After a successful parse the raw downloaded file MUST be deleted; total disk usage of the corpus MUST stay under 25 GB.
- **FR-011**: Per-document failures MUST be recorded with a reason and MUST NOT stop the rest of the run; failed documents MUST be retried on the next run.
- **FR-012**: Document text MUST be stored and handled as untrusted data.

**Retrieval**

- **FR-013**: The system MUST provide a search capability that accepts a query, optional filters (ticker, period range, document type, section, language) and a result count k (default 6), and returns chunks with source reference and score.
- **FR-014**: Search MUST combine meaning-based matching and keyword matching (with Turkish and English language handling), fuse the two rankings, and optionally rerank the top 20 candidates with a local reranking model.
- **FR-015**: Filters MUST be applied before ranking so that no result outside the filter is returned.
- **FR-016**: Query vectors MUST be cached so that repeated queries do not recompute them.
- **FR-017**: The reranking step MUST be switchable on/off by configuration, and the chosen default MUST come from the evaluation in FR-020.
- **FR-018**: The system MUST provide a document listing capability by ticker, document type and period range returning document metadata.

**Evaluation**

- **FR-019**: A versioned retrieval golden set MUST exist with at least 50 questions covering both Turkish and English and both data sources, each with its expected source chunks.
- **FR-020**: A retrieval evaluation MUST compute recall@5, recall@10 and MRR, overall and per language, for one or more configurations, and write a dated report.
- **FR-021**: CI MUST run a deterministic retrieval evaluation subset without live network or model calls and MUST fail when recall@10 regresses beyond a configured tolerance against the `main` baseline.
- **FR-022**: The chosen chunk size, k and reranker setting MUST be recorded in an ADR under `docs/adr/` with the supporting measurements.

### Key Entities

- **Company**: An issuer in the corpus; name, ticker, market (BIST or US), source identifiers (e.g. KAP member ID, SEC CIK).
- **Document**: One disclosure; company, document type, period, publication date, source URL, content hash, language, ingestion status and failure reason.
- **Section**: A named part of a document (e.g. "Item 7 MD&A"), with page range where applicable.
- **Table**: A structured table extracted from a document, stored as rows with headers, linked to its section and page.
- **Chunk**: A retrievable text unit; text, vector, document, section, page, language and full company/period metadata.
- **Financial Fact**: A structured financial line item; company, metric, period, value, unit, currency and source reference.
- **Ingestion Run**: One pipeline execution; start/end time, documents fetched, skipped, failed.
- **Golden Question**: An evaluation item; question text, language, filters, expected chunk references.
- **Evaluation Report**: Dated results of a retrieval evaluation per configuration.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The corpus covers at least 35 of the ~40 configured companies, with at least one financial report per company for each of the last 4 available quarters or 2 years.
- **SC-002**: A second ingestion run over unchanged sources creates 0 new documents, chunks or facts and modifies 0 stored records.
- **SC-003**: 100% of stored chunks and financial facts carry a resolvable source reference (document plus section or page).
- **SC-004**: Recall@10 on the golden set is measured and published in a dated report, overall and per language; the target for the chosen configuration is at least 0.80.
- **SC-005**: 95% of searches return results in under 1 second on the production server, excluding the first call after a restart.
- **SC-006**: Corpus disk usage stays under 25 GB, and no raw downloaded files remain after a successful run.
- **SC-007**: An ADR exists that names the chosen chunk size, k and reranker setting and cites the measured numbers behind each choice.

## Assumptions

- Phase 1 (walking skeleton) acceptance criteria have passed and its database, cache, worker and CI are available.
- The existing Numiver KAP/TEFAS pipeline and PDF OCR can be reused or adapted for KAP fetching and parsing.
- The US company list defaults to about 20 large, widely followed SEC filers (e.g. Apple, Microsoft), chosen by the owner during planning if not specified.
- "Last 2 years" is counted back from the ingestion date; KAP coverage targets the same window.
- Embedding and reranking models are small, multilingual and run on CPU, as fixed in PROJECT_SPEC §4; no API-based embedding is used.
- Golden set expected chunks are labelled manually by the owner (with tool assistance), since no labelled Turkish disclosure dataset exists.
- Agent use of search, answer generation, financial-facts tools and the MCP server are out of scope for this phase; only the underlying capabilities and their evaluation are delivered.
- Ingestion is triggered manually or on a schedule by the owner; there is no end-user-facing ingestion UI in this phase.
