# Contract: Agent tools, Answer schema, evals

## Tools (model-facing, `tool_schema_version = "1"`)

| Tool | Args | Result items |
|---|---|---|
| `search_documents` | `query`, `ticker?`, `period_from?`, `period_to?`, `doc_type?`, `language?`, `k?` (≤ 8) | `{cite_id: "c:<id>", text, ticker, period, doc_type, section, page}` |
| `get_financial_facts` | `ticker`, `metrics[]`, `periods[]` | `{cite_id: "f:<id>", metric, period, value, unit, currency}` |
| `list_documents` | `ticker`, `doc_type?`, `period_from?`, `period_to?` | document metadata (not citable) |

Results are sent to the model inside `<untrusted_document cite_id="…">…</untrusted_document>`.

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

Before validation, a `cite_id` written inline in `summary` (`[c:…]` or `[f:…]`, seen in
production from a model that skipped the markers) is replaced by its `[n]` marker; a cite id
without a citation entry gets the next free marker. Unknown ids are then caught by rule 1.

Validation (in code, after schema check):
1. Every `cite_id` was returned by a tool in this run.
2. Every number in `summary`/`points` appears in a cited chunk text or fact value
   (thousand/decimal separators for TR and EN normalised; percentages may be derived from two
   cited facts or two numbers in cited chunk text and must match within 0.1 pp). Financial
   tables state amounts in thousands or millions, so a number also matches a source number
   multiplied by 10^3, 10^6 or 10^9 (`551928` in a "million TL" table supports
   `551.928.000.000 TL`).
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
