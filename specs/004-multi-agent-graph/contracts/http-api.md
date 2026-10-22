# Contract: HTTP API changes (Phase 4)

Extends `specs/003-single-agent-mvp/contracts/http-api.md`. Same base URL, cookie auth,
error envelope `{"error": {"code", "message", "request_id", "details"?}}`, CORS and rate
limits. CORS methods add `PUT` and `DELETE`.

## Research

| Method | Path | Body | Result |
|---|---|---|---|
| POST | `/v1/research` | unchanged `{"question"}` | 202 `{"run_id", "quota": Quotas}`; takes one simple unit |
| GET | `/v1/research/{run_id}` | — | 200 `Run` (below) |
| GET | `/v1/research/{run_id}/stream` | `Last-Event-ID` optional | SSE ([sse-events.md](sse-events.md)) |
| POST | `/v1/research/{run_id}/approve` | `Decision` | 202 `{"run_id", "status": "running" \| "cancelled", "quota": Quotas}` |

`Run` adds `mode`, `plan`, `plan_expires_at`, `decision`, `engine`; `answer` is a
[`Report`](report.md); `citations` include `k:` calculation citations.

### `Decision`

```json
{"action": "approve" | "edit" | "cancel" | "simple", "plan": Plan}
```

- `plan` is required for `edit` and ignored otherwise. `Plan` as in
  [data-model.md](../data-model.md) (`estimate` and `status` fields are recomputed by the
  server and ignored on input).
- `simple` is valid only while the pending decision kind is `deep_quota_offer`; `approve` and
  `edit` only for kind `plan`; `cancel` for both.

### Errors

| Code | HTTP | When |
|---|---|---|
| `not_found` | 404 | unknown run or not the owner |
| `not_awaiting_decision` | 409 | run is not waiting, or action does not fit the pending kind |
| `already_decided` | 409 | another decision was accepted first |
| `plan_expired` | 410 | `plan_expires_at` has passed |
| `plan_invalid` | 422 | edited plan fails validation; `details.reasons: [{"step": "s2" \| null, "field", "reason"}]`; run stays waiting |
| `quota_exhausted` | 429 | no deep unit left at approval; run stays waiting |

## Quotas

`QuotaStatus` from Phase 3 is reused. `Quotas = {"simple": QuotaStatus, "deep": QuotaStatus}`.
`GET /v1/me` returns `"quota": Quotas` (the old single object becomes `quota.simple`; the web
app is updated in the same release).

## Memory

| Method | Path | Body | Result |
|---|---|---|---|
| GET | `/v1/me/memory` | — | 200 `Memory` |
| PUT | `/v1/me/memory/preferences` | `{"answer_language": "tr" \| "en" \| null, "default_period_range": "last_4q" \| "last_8q" \| "last_2fy" \| "last_3fy" \| null}` | 200 `Memory` |
| POST | `/v1/me/memory/watchlist` | `{"ticker": str}` | 200 `Memory` (idempotent) |
| DELETE | `/v1/me/memory/watchlist/{ticker}` | — | 200 `Memory` (idempotent) |
| DELETE | `/v1/me/memory` | — | 204; removes watchlist and preferences |

`Memory = {"watchlist": [{"ticker", "name", "market": "bist" | "us", "added_at"}],
"preferences": {"answer_language", "default_period_range"}, "limits": {"watchlist_max": 50}}`

Errors: `company_not_covered` (422) for a ticker not in `companies`; `watchlist_full` (422)
at 50 items; `validation_error` for bad enum values.

## Companies

| Method | Path | Query | Result |
|---|---|---|---|
| GET | `/v1/companies` | `q` (1–50 chars, optional), `limit` (1–20, default 10) | 200 `{"items": [{"ticker", "name", "market"}]}` |

Matches ticker prefix or name substring (case- and Turkish-dotless-i-insensitive), corpus
companies only. Authenticated, counts against rate limits.

## New stable error codes

`already_decided`, `not_awaiting_decision`, `plan_expired`, `plan_invalid`, `watchlist_full`,
`company_not_covered` (HTTP), and run-level `sandbox_unavailable`, `interrupted_limit`
(appear in `error` events and `Run.error_code`). Messages in Turkish and English in
`api/errors.py`.
