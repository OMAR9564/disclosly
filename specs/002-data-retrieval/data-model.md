# Data Model: Data + Retrieval

Migration `0002_corpus`. Extensions: `vector`, `unaccent`. Text search configs `tr_unaccent`
and `en_unaccent` (snowball turkish/english + unaccent).

## companies

| Field | Type | Rules |
|---|---|---|
| id | bigint PK | |
| ticker | text | unique, uppercase |
| name | text | |
| market | enum `bist`, `us` | |
| kap_member_id | text null | required when market = bist |
| sec_cik | text null | 10 digits, required when market = us |

## documents

| Field | Type | Rules |
|---|---|---|
| id | bigint PK | |
| company_id | FK companies | |
| source | enum `kap`, `sec` | |
| doc_type | enum `10-K`, `10-Q`, `kap_financial_report`, `kap_material_event`, `kap_annual_report`, `kap_prospectus`, `kap_fund_report` | |
| period | text | `2025-Q3`, `2025-FY` |
| published_at | timestamptz | |
| source_url | text | |
| content_hash | char(64) | sha256 of raw bytes; **unique (source_url, content_hash)** |
| language | enum `tr`, `en` | |
| status | enum `fetched`, `parsed`, `chunked`, `embedded`, `failed` | |
| failure_reason | text null | set when status = failed |
| supersedes_id | FK documents null | earlier version at the same URL |
| attempts | int | retry counter |

State: `fetched → parsed → chunked → embedded`; any step → `failed`; `failed → fetched` on the
next run (FR-011). A new hash at a known URL creates a new row with `supersedes_id` set.

## sections

`id`, `document_id`, `ordinal`, `label` (e.g. `Item 7`, `body`), `title`, `page_start`,
`page_end`.

## tables

`id`, `document_id`, `section_id`, `page`, `header` (jsonb list), `rows` (jsonb list of lists).

## chunks

| Field | Type | Rules |
|---|---|---|
| id | char(64) PK | hash(document_id, chunker_version, ordinal) |
| document_id, section_id | FK | |
| chunker_version | text | e.g. `v1-600-64`; several versions may coexist for sweeps |
| ordinal | int | |
| kind | enum `text`, `table` | table chunks never split a row |
| text | text | |
| token_count | int | ≤ configured max |
| page | int null | |
| company_id, ticker, period, doc_type, language | denormalised | for pre-ranking filters |
| embedding | vector(384) null | null until embedded |
| tsv | tsvector | generated from text with config by language |

Indexes: HNSW on `embedding vector_cosine_ops`; GIN on `tsv`; btree on
`(ticker, period, doc_type, language, chunker_version)`.

## financial_facts

`id`, `company_id`, `metric` (canonical name: `revenue`, `net_income`, `total_debt`,
`operating_income`, `depreciation_amortization`, `ebitda`, ...), `period`, `value` numeric,
`unit`, `currency`, `source_document_id` FK, `source_ref` (XBRL concept + accession, or KAP
section/page), `derived` bool. Unique `(company_id, metric, period, source_document_id)`.

## ingestion_runs

`id`, `started_at`, `finished_at`, `trigger` (`manual`, `schedule`), `fetched`, `skipped`,
`failed`, `notes` jsonb.

## Evaluation (files, not tables)

- Golden question: see [contracts/golden-set.md](contracts/golden-set.md).
- Evaluation report: `evals/reports/retrieval-YYYY-MM-DD.json` + `.md`.
