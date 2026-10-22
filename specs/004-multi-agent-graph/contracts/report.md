# Contract: Report and citations (Phase 4)

`Report` replaces the Phase 3 `Answer` as the final output of both modes. It is a superset:
a Phase 3 `Answer` JSON is a valid `Report` with the new fields at their defaults, so old run
rows stay readable.

```json
{
  "language": "tr" | "en",
  "status": "completed" | "partial" | "refused" | "no_evidence",
  "summary": "string with [n] markers",
  "points": [{"text": "…[n]…", "cite_ids": ["f:12", "k:1"]}],
  "tables": [
    {
      "title": "string",
      "columns": ["Dönem", "Net borç", "FAVÖK", "Net borç / FAVÖK"],
      "rows": [[{"text": "2025-Q4"}, {"text": "41,2 milyar TL", "cite_id": "f:88"}, {"text": "…", "cite_id": "f:91"}, {"text": "1,9", "cite_id": "k:3"}]]
    }
  ],
  "chart": {"kind": "line" | "bar", "table": 0, "x_column": "Dönem", "series": ["Net borç / FAVÖK"]} | null,
  "confidence": {"level": "high" | "medium" | "low", "note": "string"},
  "unverified": ["string"],
  "citations": [{"marker": 1, "cite_id": "k:3"}],
  "disclaimer": "Yatırım tavsiyesi değildir / Not investment advice"
}
```

Limits: ≤ 4 tables, ≤ 8 columns, ≤ 24 rows per table, ≤ 1 chart, ≤ 4 series, `note` ≤ 400
chars, `unverified` ≤ 20 items.

## Rules checked in code (after the schema)

1. Phase 3 rules still apply: every `cite_id` was produced in this run (by a tool or as a
   calculation), the disclaimer is server-set, `language` matches the question.
2. Every number in `summary`, `points` and table cells is either in a cited fact or chunk
   (Phase 3 matcher, including scale words and 10^3/10^6/10^9 scaling) or equals the output of
   a **verified** calculation it cites (`k:`), at the precision shown.
3. A table cell containing a number has a `cite_id`.
4. `chart.table` exists; `x_column` and every `series` are columns of that table; every series
   cell is numeric. Plotted values are read from those cells, never sent separately.
5. `status = partial` ⇒ `confidence.level = low` and `unverified` is non-empty, or a limit was
   hit (`usage.limit_hit`).
6. `refused` and `no_evidence` reports have no tables and no chart.

Failure: one re-ask with the errors if a model call is left; otherwise `finalize_partial`
drops failing points, rows and the chart and returns a `partial` report.

## Citations (`Citation[]` in `final` and `GET /v1/research/{id}`)

- `c:<chunk_id>` and `f:<fact_id>`: unchanged from Phase 3 (`excerpt`, `source`).
- `k:<n>`: `{"cite_id": "k:3", "excerpt": "Net borç / FAVÖK = net_debt / ebitda",
  "source": {"kind": "calculation", "name": "net_debt_to_ebitda", "inputs": [{"name":
  "net_debt", "cite_id": "f:88", "value": "41200000000"}, …], "code": "…", "output": "1.9"}}`.
  Every input `cite_id` is also present in the citation list, so the popover can open it.
