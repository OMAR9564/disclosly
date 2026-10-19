# Contract: Retrieval golden set and cassettes

## `evals/datasets/retrieval_golden.jsonl`

One JSON object per line:

```json
{
  "id": "tr-thyao-net-debt-2025q2",
  "language": "tr",
  "question": "THYAO 2025 ikinci çeyrek net borç ne kadar?",
  "filters": {"tickers": ["THYAO"]},
  "expected": [
    {"ticker": "THYAO", "doc_type": "kap_financial_report", "period": "2025-Q2",
     "page": 14, "quote": "Net borç"}
  ]
}
```

Rules: ≥ 50 cases; ≥ 20 per language; both sources covered; `quote` is verbatim text from the
document (≥ 3 words preferred). A hit = retrieved chunk from the same document that contains
the normalised quote (case-folded, whitespace-collapsed, accent-insensitive).

## `evals/cassettes/retrieval/`

- `corpus.jsonl` — fixture documents and chunks with stored vectors (public filings only).
- `queries/<id>.json` — `{"model": "...", "vector": [...384 floats]}`.
- `rerank/<id>.json` — `{"model": "...", "scores": {"<chunk_id>": float}}`.

## Report `evals/reports/retrieval-YYYY-MM-DD.json`

`{"date", "corpus": "full|cassette", "configs": [{"chunker_version", "rerank", "k",
"recall_at_5", "recall_at_10", "mrr", "by_language": {"tr": {...}, "en": {...}}}]}`.

Baseline: `evals/baselines/retrieval.json` holds the `main` recall@10 for the cassette corpus;
CI fails when the current value is lower by more than `0.02`.
