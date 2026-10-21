# Contract: HTTP API (Phase 3)

Base: `https://api.disclosly.com`. Auth: `session` cookie (HttpOnly, Secure, SameSite=Lax,
Domain=.disclosly.com). CORS: `https://disclosly.com` with credentials; methods GET, POST.
All errors use the existing envelope `{"error": {"code", "message", "request_id"}}`.

## Auth

| Method | Path | Body / query | Result |
|---|---|---|---|
| POST | `/v1/auth/{provider}/start` | `{"turnstile_token", "return_to"}`; provider ∈ `google`, `github` | 200 `{"authorize_url"}` |
| GET | `/v1/auth/{provider}/callback` | `code`, `state` | 302 to `return_to` (web origin only), sets cookies; not allowlisted → 302 to `/beta-closed` |
| POST | `/v1/auth/refresh` | — | 204, rotates cookies |
| POST | `/v1/auth/logout` | — | 204, clears cookies, revokes refresh |

## Research

| Method | Path | Body | Result |
|---|---|---|---|
| POST | `/v1/research` | `{"question": str (3–1000)}` | 202 `{"run_id", "quota": QuotaStatus}` |
| GET | `/v1/research/{run_id}/stream` | header `Last-Event-ID` optional | `text/event-stream` (sse-events.md) |
| GET | `/v1/research/{run_id}` | — | 200 `Run` (status, question, language, answer, citations[], usage, created_at) |

Only the run owner can read a run (others get `not_found`).

## Me

| Method | Path | Result |
|---|---|---|
| GET | `/v1/me` | 200 `{"display_name", "email_masked", "language", "quota": QuotaStatus}` |
| PUT | `/v1/me` | body `{"language": "tr"\|"en"}` → 200 |

`QuotaStatus` = `{"limit": int, "used": int, "remaining": int, "resets_at": ISO-8601}`.

## New error codes

| Code | HTTP | When |
|---|---|---|
| `unauthorized` | 401 | missing/expired session |
| `beta_closed` | 403 | email not on allowlist |
| `bot_check_failed` | 403 | Turnstile token invalid |
| `quota_exhausted` | 429 | daily quota used; message includes reset time |
| `rate_limited` | 429 | per-IP/per-user limit; `Retry-After` header |
| `providers_unavailable` | 503 | all model providers failed or exhausted |

Messages are returned in the user's language (`Accept-Language` or profile).
