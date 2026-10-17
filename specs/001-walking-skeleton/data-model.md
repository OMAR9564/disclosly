# Data Model: Phase 1 — Walking Skeleton

**Feature**: [spec.md](./spec.md) | **Date**: 2026-10-17

Phase 1 creates **no database tables**. The entities below are response payloads, configuration
and deploy records. They are defined as Pydantic models (backend) and TypeScript types (web).
Wire formats: [contracts/openapi.yaml](./contracts/openapi.yaml).

## HealthStatus (`GET /healthz`)

| Field | Type | Rules |
|---|---|---|
| `status` | `"ok"` | Always `"ok"` when the process can answer. Liveness does no dependency I/O. |
| `version` | string | Git commit SHA the image was built from (`APP_VERSION`), 7–40 hex chars, or `"dev"` locally. |
| `service` | `"disclosly-api"` | Constant. |

## ReadinessStatus (`GET /readyz`)

| Field | Type | Rules |
|---|---|---|
| `status` | `"ready"` \| `"not_ready"` | `"ready"` only if every entry in `checks` is `"ok"`. HTTP 200 vs 503. |
| `version` | string | Same as HealthStatus. |
| `checks` | map of DependencyName to DependencyCheck | Keys: `postgres`, `redis`. |

**DependencyCheck**

| Field | Type | Rules |
|---|---|---|
| `status` | `"ok"` \| `"error"` | `"error"` on exception or timeout (2 s per check). |
| `latency_ms` | number ≥ 0 | Wall-clock time of the check. |
| `error` | string \| null | Short, stable reason (`"timeout"`, `"connection_refused"`, `"auth_failed"`, `"unknown"`). Never contains a connection string, host password or stack trace. |

## ErrorEnvelope (every non-2xx JSON response)

| Field | Type | Rules |
|---|---|---|
| `error.code` | string | Stable snake_case code from the catalogue below. |
| `error.message` | string | Human-readable English text; no internals. |
| `error.request_id` | string | Same value as the `X-Request-ID` response header. |
| `error.details` | list \| null | Only for `validation_error`: field path + message. |

**Error code catalogue (Phase 1)**

| Code | HTTP | When |
|---|---|---|
| `not_found` | 404 | Unknown route. |
| `method_not_allowed` | 405 | Wrong method on a known route. |
| `validation_error` | 422 | Request validation failure. |
| `dependency_unavailable` | 503 | Used in logs for readiness failures. `/readyz` itself returns ReadinessStatus. |
| `internal_error` | 500 | Any unhandled exception. The details are logged server-side only. |

## Settings (environment configuration)

Full variable list and required/secret flags: [contracts/configuration.md](./contracts/configuration.md).

Validation rules:
- Missing required variable: the process exits non-zero at startup. The log names the variable,
  never its value (FR-010).
- `CORS_ALLOWED_ORIGINS` is a comma-separated list of absolute `https://` origins
  (`http://localhost:*` is allowed only when `APP_ENV=development`).
- `APP_ENV` is one of `development`, `test`, `production`.
- Secret-typed fields (`SecretStr`) never appear in `repr`, logs or error messages.

## RequestLogRecord (one JSON log line per request)

| Field | Type | Rules |
|---|---|---|
| `ts` | ISO-8601 UTC | |
| `level` | string | `info` for 2xx–4xx, `error` for 5xx. |
| `msg` | `"request"` | |
| `request_id` | string | From `X-Request-ID` if it is valid (≤ 64 chars, `[A-Za-z0-9-]`), else a new UUID4. |
| `method`, `route`, `status`, `latency_ms` | | `route` is the route template, not the raw path. |
| `version` | string | `APP_VERSION`. |

Redaction: email-like values and fields matching `*key*`, `*token*`, `*secret*`, `*password*`,
`authorization` are replaced with `"[REDACTED]"`.

## Deployment (record lives in GitHub Actions run + server files, not the DB)

| Field | Where | Rules |
|---|---|---|
| `commit_sha` | workflow input | The commit that passed CI on `main`. |
| `image` | GHCR | `ghcr.io/<owner>/disclosly-api:<commit_sha>` |
| `previous_tag` | server `~/disclosly/.deploy/previous` | Written before the switch. |
| `current_tag` | server `.env` `API_IMAGE_TAG` | |
| `result` | workflow conclusion | `succeeded` \| `failed` \| `rolled_back` |

**State transitions**

```text
pending ──(pull + migrate ok)──> switching ──(healthz reports new version ≤ 60 s)──> succeeded
   │                                │
   └──(pull or migrate fails)──> failed (previous version untouched)
                                    └──(health check fails)──> rolled_back (previous tag restored, job fails)
```
