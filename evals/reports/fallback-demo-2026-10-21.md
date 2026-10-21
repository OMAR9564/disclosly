# Fallback demo — 2026-10-21

Task T030 (FR-028, SC-004). A live run against the free providers, with a 429 injected
locally into the first provider of the `cheap` class.

```bash
uv run --env-file .env python -m llm.demo_fallback --fail-first
```

## Output

```text
provider failed
injected 429 into: gemini-flash-lite
answered by:       groq-llama (openai/gpt-oss-20b)
fallback_from:     gemini-flash-lite
latency_ms:        555.251
tokens:            84 in / 110 out
trace_id:          - (no OTel SDK configured)
content:           A cash flow statement shows how cash enters and leaves a company during a period.
```

## Result

- The 429 on `gemini-flash-lite` was classified as rate limited and the router moved on.
- `groq-llama` answered; `fallback_from` records the failed provider.
- The fallback cost one extra round trip at most: the injected 429 never left the machine,
  and the answer took 555 ms end to end.

## Trace

No trace link: only `opentelemetry-api` is installed, so spans are no-ops and the demo has no
trace id. The span attributes (`disclosly.fallback_from`, `disclosly.cache_hit`,
`disclosly.would_have_cost_usd`) are covered by `tests/unit/llm/test_telemetry.py`. A trace
link can be added once an OTel SDK and exporter are configured.
