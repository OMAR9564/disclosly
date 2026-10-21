# Data Model: Single-Agent MVP

Migration `0003_users_runs_cache`. Phase 2 tables (`documents`, `chunks`, `financial_facts`)
are unchanged.

## users

| Field | Type | Rules |
|---|---|---|
| id | uuid PK | |
| email | text, unique | lower-cased; never logged (masked `a***@d***.com`) |
| display_name | text | ≤ 200 chars, any Unicode |
| auth_provider | enum `google`, `github` | |
| provider_subject | text | unique with `auth_provider` |
| language | enum `tr`, `en` | default `tr` |
| created_at, last_seen_at | timestamptz | |

Beta access is not a column: it is derived from `beta_allowlist` at request time.

## beta_allowlist

| Field | Type | Rules |
|---|---|---|
| email | text PK | lower-cased |
| added_at | timestamptz | |
| note | text, nullable | owner's note, no personal data beyond the email |

## runs

| Field | Type | Rules |
|---|---|---|
| id | uuid PK | |
| user_id | uuid FK users | |
| question | text | 3–1,000 chars |
| language | enum `tr`, `en` | detected from question |
| status | enum | see transitions |
| answer | jsonb, nullable | `Answer` (contracts/agent-answer.md) |
| usage | jsonb | model_calls, tool_calls, tokens_in, tokens_out, providers[], cache_hits, time_to_first_event_ms, total_ms |
| error_code | text, nullable | stable code from contracts/http-api.md |
| created_at, finished_at | timestamptz | |

**Status transitions**: `running → completed | partial | refused | no_evidence | failed`. `refused` = advice
question answered with information only; `no_evidence` = no supporting source found. At API startup, `running` rows older than the deadline
become `failed` (`interrupted`).

## run_citations

| Field | Type | Rules |
|---|---|---|
| run_id | uuid FK runs | |
| cite_id | text | `c:<chunk_id>` or `f:<fact_id>`; must exist |
| excerpt | text | ≤ 1,500 chars, copied at answer time so the popover works even if the corpus is re-chunked |
| source | jsonb | document title, ticker, period, doc_type, section, page, source_url |

PK (`run_id`, `cite_id`).

## llm_cache

| Field | Type | Rules |
|---|---|---|
| key | char(64) PK | SHA-256 (contracts/model-router.md) |
| provider, model | text | |
| response | jsonb | normalised `ChatResponse` |
| hits | int | incremented on hit |
| created_at, last_hit_at | timestamptz | rows not hit for 30 days are deleted by a nightly job |

## Redis keys (non-durable)

| Key | Value | TTL |
|---|---|---|
| `llm:cooldown:{provider}` | `1` | Retry-After or 60 s |
| `llm:usage:{provider}:{yyyymmdd}` | counter | 48 h |
| `llm:cache:{key}` | response JSON | 24 h |
| `quota:{user_id}:{yyyymmdd}` | counter (TRT day) | 48 h |
| `rl:user:{user_id}:{minute}`, `rl:ip:{ip_hash}:{minute}` | counter | 120 s |
| `allow:{email_hash}` | `0`/`1` | 60 s |
| `refresh:{token_id}` | user_id | 7 days |
| `run:{run_id}:events` | stream | 24 h after finish |
