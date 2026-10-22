# Data Model: Multi-Agent Research (Phase 4)

Migration `api/migrations/versions/0004_graph_runs_memory.py` changes the Phase 3 tables
(`specs/003-single-agent-mvp/data-model.md`) and adds the memory tables. Graph checkpoints are
stored by the LangGraph saver in schema `agent_graph` (research §R1), outside Alembic.

## Changed: `runs`

| Column | Type | Rules |
|---|---|---|
| `status` | enum `run_status` | adds `awaiting_approval`, `cancelled`, `expired` (see transitions) |
| `mode` | enum `run_mode` (`simple`, `deep`), nullable | set by the router; null until then and for `single` engine runs that predate it |
| `engine` | enum `run_engine` (`graph`, `single`), default `graph` | which engine ran it (research §R15) |
| `plan` | jsonb, nullable | the current `Plan` (below); replaced on edit and on verifier revisions |
| `plan_expires_at` | timestamptz, nullable | set when a decision is requested; required when `status = awaiting_approval` |
| `decided_at` | timestamptz, nullable | first decision time |
| `decision` | enum `plan_decision` (`approve`, `edit`, `cancel`, `simple`), nullable | first decision |
| `deadline_at` | timestamptz, nullable | absolute run deadline (research §R3) |
| `resume_count` | integer, default 0 | incremented on each resume; > 3 fails the run with `interrupted_limit` |
| `answer` | jsonb | now holds a `Report` (superset of the Phase 3 `Answer`; old rows stay readable) |
| `usage` | jsonb | adds `nodes: {node: {model_calls, tool_calls, ms}}`, `sandbox_runs`, `verify_rounds`, `resumes` |

Constraints: `ck_runs_awaiting_has_expiry`:
`status <> 'awaiting_approval' OR plan_expires_at IS NOT NULL`. Index
`ix_runs_status_plan_expires_at (status, plan_expires_at)` for the expiry sweep.

### Status transitions

```text
running ──(deep plan or quota offer)──→ awaiting_approval
awaiting_approval ──approve/edit/simple──→ running
awaiting_approval ──cancel──→ cancelled
awaiting_approval ──expiry sweep──→ expired
running ──→ completed | partial | refused | no_evidence | failed
```

`cancelled`, `expired`, `completed`, `partial`, `refused`, `no_evidence` and `failed` are
final. A restart never moves a run to `failed` by itself; the supervisor resumes it (research
§R7) unless `resume_count` passes the limit.

## Changed: `run_citations`

| Column | Change |
|---|---|
| `cite_id` | check becomes `^[cfk]:.+` (`k:` = calculation) |
| `excerpt` | for `k:`, the calculation's description and formula, ≤ 1,500 chars |
| `source` | for `k:`: `{"kind": "calculation", "name", "inputs": [{"name", "cite_id", "value"}], "code", "output"}` (code ≤ 4,000 chars) |

## New: `watchlist_items`

| Column | Type | Rules |
|---|---|---|
| `user_id` | uuid FK `users.id` on delete cascade | |
| `company_id` | bigint FK `companies.id` on delete cascade | corpus companies only |
| `added_at` | timestamptz default now() | |

PK `(user_id, company_id)`. At most 50 rows per user, checked in the insert transaction
(`watchlist_full`).

## New: `user_preferences`

| Column | Type | Rules |
|---|---|---|
| `user_id` | uuid PK FK `users.id` on delete cascade | |
| `answer_language` | enum `language`, nullable | used only when a question's language cannot be detected |
| `default_period_range` | enum `period_range` (`last_4q`, `last_8q`, `last_2fy`, `last_3fy`), nullable | null means `last_4q` |
| `updated_at` | timestamptz default now() | |

"Delete all memory" removes the user's `watchlist_items` rows and `user_preferences` row.

## Graph state (`agent/graph/state.py`, checkpointed, not a table)

`ResearchState` follows PROJECT_SPEC §6.1, with Pydantic models for every field:

| Field | Type | Notes |
|---|---|---|
| `run_id`, `user_id` | str | |
| `question` | str | |
| `language` | `tr` \| `en` | |
| `mode` | `simple` \| `deep` \| null | |
| `router` | `RouterDecision` \| null | mode, tickers, periods, metrics, `uses_watchlist`, `memory_intent` |
| `memory` | `MemorySnapshot` | watchlist tickers, preferences, read once at start |
| `plan` | `Plan` \| null | |
| `plan_approved` | bool | |
| `evidence` | `list[SeenItem]` (reducer: add, dedup by `cite_id`) | |
| `findings` | `list[Finding]` | replaced each analyst round |
| `calculations` | `list[Calculation]` (reducer: add) | |
| `verification` | `VerificationResult` \| null | |
| `verify_rounds` | int | ≤ 2 |
| `draft` | `Report` \| null | |
| `budget` | `Budget` | `max_model_calls`, `max_steps`, `deadline_seconds` |
| `deadline_at` | datetime \| null | absolute |
| `usage` | `Usage` | model_calls, tool_calls, sandbox_runs, tokens, providers, per-node |
| `limit_hit` | `model_calls` \| `deadline` \| `steps` \| null | routes to `finalize_partial` |
| `errors` | `list[str]` (reducer: add) | stable codes only |

## Value objects

- **Plan**: `steps: list[PlanStep]` (1–8), `estimate: {typical: int, max: int}`,
  `edited: bool`, `revision: int`.
- **PlanStep**: `id` (`s1`…), `kind` (`facts` \| `search` \| `calculate`), `goal` (1–300
  chars), `tickers` (0–4, upper-case), `periods` (0–8, `^\d{4}-(Q[1-4]|FY)$`), `metrics`
  (subset of `facts.KNOWN_METRICS`), `status` (`pending` \| `done` \| `not_covered` \|
  `failed`).
- **Finding**: `id`, `text`, `cite_ids` (≥ 1), `calcs` (names).
- **Calculation**: `cite_id` (`k:<n>`), `name` (`^[a-z][a-z0-9_]{0,39}$`), `description`,
  `inputs: [{name, cite_id, value: Decimal}]`, `code` (≤ 4,000 chars), `output: Decimal | null`,
  `error: str | null`, `verified: bool`.
- **VerificationResult**: `round`, `passed`, `failures: [{kind: input_mismatch |
  recompute_mismatch | unsupported_claim | sandbox_error, ref, reason}]`.
- **Report**: see [contracts/report.md](contracts/report.md).
- **MemorySnapshot**: `watchlist: list[str]` (tickers), `answer_language`, `default_period_range`.

## Redis keys (new)

| Key | Value | TTL |
|---|---|---|
| `quota:deep:{user_id}:{yyyymmdd}` | deep questions used (Europe/Istanbul day) | 48 h |
| `run:{run_id}:lease` | holder process id | 30 s, renewed every 10 s |
| `supervisor:sweep` | lock for the expiry and retention sweep | 60 s |
