# Contract: Agent tools, Answer schema, evals

## Tools (model-facing, `tool_schema_version = "1"`)

| Tool | Args | Result items |
|---|---|---|
| `search_documents` | `query`, `ticker?`, `period_from?`, `period_to?`, `doc_type?`, `language?`, `k?` (≤ 8) | `{cite_id: "c:<id>", text, ticker, period, doc_type, section, page}` |
| `get_financial_facts` | `ticker`, `metrics[]`, `periods[]` | `{cite_id: "f:<id>", metric, period, value, unit, currency}` |
| `list_documents` | `ticker`, `doc_type?`, `period_from?`, `period_to?` | document metadata (not citable) |

Results are sent to the model inside `<untrusted_document cite_id="…">…</untrusted_document>`;
a chunk whose unit is known also carries `amounts_in="…"` (see rule 2 below). When the search
worker does not answer, `search_documents` and `list_documents` return a tool error ("document
search is temporarily unavailable") and the run goes on.

A fact's text names its table column and, when it differs from the fact's period, the report it
came from: "Net dönem kârı (2024-FY; Önceki Dönem 01.01.2024 - 31.12.2024; 2025-FY raporu)".
Turkish reports restate the prior period (IAS 29 inflation adjustment), so one period can have
two values; the figure as first reported is listed first. Found in the T046 test (2026-10-22):
BIMAS 2024 net profit 18.6 bn TL as reported vs 24.4 bn TL restated in the 2025 report.

## Answer (final model output, validated)

```json
{
  "language": "tr",
  "status": "completed | partial | refused | no_evidence",
  "summary": "short answer text with markers [1] [2]",
  "points": [{"text": "...", "cite_ids": ["f:123"]}],
  "citations": [{"marker": 1, "cite_id": "f:123"}],
  "disclaimer": "Yatırım tavsiyesi değildir / Not investment advice"
}
```

Before validation, a `cite_id` written inline in `summary` (`[c:…]`, `[f:…]`, or a bare
64-character chunk id, seen in production from models that skipped the markers) is replaced by its `[n]` marker; a cite id
without a citation entry gets the next free marker. Unknown ids are then caught by rule 1.

Validation (in code, after schema check):
1. Every `cite_id` was returned by a tool in this run.
2. Every number in `summary`/`points` appears in a cited chunk text or fact value
   (thousand/decimal separators for TR and EN normalised; percentages may be derived from two
   cited facts or two numbers in cited chunk text and must match within 0.1 pp).
   Units are checked too (changed 2026-10-22: production answered "56016 bin TL" from a
   million-TL table). A source amount is its number times its scale: a scale word after it
   ("23,1 milyar TL", "$391,035 million"), the KAP report's presentation unit ("Sunum Para
   Birimi: 1.000.000 TL"), the nearest table header ("(In millions, except ...)") in the chunk
   or in up to 3 chunks before it in the same section (a split table), or a fact's unit. The
   chunk's unit is sent to the model as `amounts_in`. An answer figure with a scale word or a
   currency must equal a source amount, rounded to the figure's own precision
   (`31,778` in millions supports "$31.8 billion"). A bare figure must equal a number as
   written in the source or a source amount. When a table's unit is unknown, only its bare
   numbers are accepted. Day numbers in dates ("March 31", "31 Mart") are not figures.
3. `disclaimer` equals the fixed string (the server overwrites it regardless).
4. `language` matches the detected question language.

Failure → one re-ask with the validation errors, only if a model call is left in the run budget (the re-ask counts toward it; otherwise go straight to `partial`); second failure → `status = partial` answer
containing only the validated points. If nothing survives (no summary, no points), the
summary is a fixed notice that no verified answer could be produced, in the run's language.

## Eval cassettes

`evals/cassettes/agent/<case_id>.json`: recorded `ChatRequest` key → `ChatResponse` pairs plus
tool results. Datasets (JSONL): `answers_golden` (`question`, `language`,
`expected_numbers[{value, tolerance}]`, `expected_cite_ids[]`), `advice_refusal` (`question`,
`language`), `injection` (`question`, `poisoned_chunk`, `forbidden_behaviour`). Metrics:
citation coverage, numeric match rate, refusal rate, injection pass rate, model calls per run.
Auth headers are stripped before writing cassettes.
