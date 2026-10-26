# Single agent vs multi-agent graph

Latest report: [evals/reports/2026-10-26-single-vs-multi.md](../../evals/reports/2026-10-26-single-vs-multi.md)
(machine-readable copy next to it).

## Method

Both engines answer the same 87 cases: 23 from `answers_golden` (Phase 3 single-company
questions), 29 from `deep_golden` (multi-company, multi-period and ratio questions), 20 from
`advice_refusal` and 15 from `injection`. Cases that only one engine supports, such as plan
edits, are left out. Each engine replays its own recorded run: `evals/cassettes/agent/` for the
single agent and `evals/cassettes/graph/` for the graph. Both use the same corpus and the same
free-tier model router. The graph approves its proposed plan automatically.

Both engines are scored by the same code as their CI gates (`evals/agent.py`,
`evals/graph.py`). A case counts as a win for the graph when it matches more golden numbers
(or passes where the single agent fails), or when it matches the same numbers and every figure
it states is supported. Running more model calls alone does not count as a loss.

## Results (2026-10-26)

| Metric | single | graph |
|---|---|---|
| Numeric match, `answers_golden` | 0.625 | 0.625 |
| Numeric match, `deep_golden` | 0.476 | 0.805 |
| Citation coverage | 1.0 | 1.0 |
| Verified-number rate (derived numbers from a checked calculation) | 0.0 | 1.0 |
| Refusal rate | 1.0 | 1.0 |
| Injection pass rate | 1.0 | 1.0 |
| Partial rate (golden and deep) | 0.25 | 0.27 |
| Model calls per run | 2.6 | 4.32 |
| Most model calls in one run | 4 | 12 |
| Latency p50 / p95 | 23.8 s / 81.8 s (deep only, 29 runs) | not recorded |

Graph against single: 15 wins, 6 losses, 66 ties. On `deep_golden` the graph wins 13 cases,
loses 3 and ties 13. On `answers_golden` it wins 2, loses 3 and ties 18. Refusal and injection
cases are all ties.

## Where each engine does better

- On deep questions the graph matches 80% of golden numbers and the single agent 48%. The
  planner splits a question into one step per company and period, so the graph keeps fetching
  facts until each step is covered. The single agent stops at its four model calls.
- Every derived number the graph states (growth, margin, net debt / EBITDA) comes from a
  sandbox calculation the verifier checked. The single agent has no calculation step: when it
  states a ratio, the model computed it.
- On simple questions both engines score 0.625. The graph's simple path stays within 4 model
  calls.
- Neither engine gave advice or followed injected text, and neither stated a number without a
  source.

## Where the graph does worse

- Cost. The graph uses 1.7 times as many model calls per run (4.32 against 2.6), and one deep
  run used 12. On the free tiers this is the main limit on how many deep questions a day the
  service can answer.
- Simple questions end partial more often. The graph lost `tr-enkai-kurumsal-yonetim-notu`,
  `tr-sahol-pepper-solar` and `en-brk-homeservices-agents`, which the single agent answered.
  The simple path's single search found less than the single agent's own searches.
- Some deep runs still miss. It lost `tr-deep-akbnk-garan-net-kar` (partial, 0 of 3 numbers),
  `en-deep-hd-ebitda-trend` (no evidence) and `en-deep-meta-net-margin-change` (1 of 3
  numbers). In all three the single agent found the figures.

## Limits of this comparison

- Latency was not recorded in cassettes before this comparison. Only the 29 single-agent deep
  runs have it. The graph's deep latency will come from production runs (`runs.usage`) in the
  Phase 4 acceptance report.
- The watchlist and period-less cases use the graph's memory. The single agent has none, so
  it loses those cases by design (`tr-deep-watchlist-net-kar`).
- Every run uses free-tier models, so results move with provider availability on the
  recording day. Each engine has one recording per case.

## Rerun

Replay (no network, also run in CI):

```bash
uv run python -m evals.run compare --replay
```

Re-record a case live (needs `DATABASE_URL`, provider keys, and a search worker or the
retrieval models):

```bash
uv run python -m evals.record_agent --dataset deep_golden --case <id> --overwrite
uv run python -m evals.record_graph --dataset deep_golden --case <id> --overwrite
```
