# Research: Multi-Agent Research (Phase 4)

Decisions for [plan.md](plan.md). Each entry: decision, rationale, alternatives considered.
Owner approvals given on 2026-10-22: LangGraph with the Postgres checkpointer (R1), the
sandbox service with Docker (R8), and a hand-written SVG chart (R12).

## R1. Orchestration: LangGraph with the Postgres checkpointer

**Decision**: Build the graph with `langgraph` 1.2.x (`StateGraph`) and persist it with
`langgraph-checkpoint-postgres` 3.2.x (`AsyncPostgresSaver` on a `psycopg_pool`
`AsyncConnectionPool`). The thread id is the run id. Nodes run with
`durability="sync"`, so a checkpoint is written before the next node starts. Checkpoint tables
live in their own Postgres schema `agent_graph` (connection option
`-c search_path=agent_graph`), created by the saver's `setup()` in the migrate step. Alembic
keeps owning `public`, and the schema comparison in `tests/integration/test_migrations.py` stays
unchanged. Nodes call our `ModelRouter`; no LangChain model wrappers or LangChain tools are
used. LangSmith tracing stays off: the settings loader refuses to start when
`LANGSMITH_TRACING` or `LANGCHAIN_TRACING_V2` is truthy, and a unit test checks this.

New Python dependencies (approved 2026-10-22): `langgraph`, `langgraph-checkpoint-postgres`.
They pull in `langgraph-checkpoint`, `langgraph-prebuilt`, `langgraph-sdk`, `langchain-core`,
`langsmith`, `psycopg-pool`, `orjson`, `ormsgpack` and `xxhash`. API memory after import is
measured in a task and recorded in the README; the `api` limit stays 512 MB unless the
measurement says otherwise (then the owner decides).

**Rationale**: PROJECT_SPEC §4 fixes LangGraph. `interrupt()` with `Command(resume=…)` gives
plan approval, and the checkpointer gives resume after restart (US3), which is the Phase 4
acceptance criterion. Writing both by hand is the riskiest part of the phase.

**Alternatives considered**: Own state machine with a `run_checkpoints` table (no
dependency, but HITL, resume and their edge cases would be ours to write and test; rejected by
the owner). Running the graph in an arq worker (arq retries jobs on restart, but it adds a
container and moves runs away from the SSE connection; ADR 0003 §5 keeps runs in the API).

## R2. Graph shape

**Decision**:

```text
START → router ─┬─ refused ───────────────────────────────→ writer → END
                ├─ memory_only (add/remove watchlist) ──────→ writer → END
                ├─ simple ─→ retriever_simple → writer_simple → END
                └─ deep ──→ quota_gate ─┬─ (deep quota 0) → decision[offer] ─┬─ simple → retriever_simple …
                                         │                                    └─ cancel → END
                                         └─ planner → decision[plan] ─┬─ approve/edit → retriever → analyst → verifier ─┬─ pass → writer → END
                                                                       └─ cancel → END                                     └─ fail (round < 2) → planner (revise, no new approval)
Any node: budget or deadline hit → finalize_partial → END
```

- **router** (1 `cheap` call): returns `RouterDecision` (mode, tickers, periods, metrics,
  `uses_watchlist`, `memory_intent`). Language and the advice check stay deterministic first
  (`agent/advice.py`); the model call refines mode and entities. Advice → `refused` without a
  plan.
- **Simple path** (PROJECT_SPEC §6.2: Router → Retriever → Writer, ≤ 4 calls): `retriever_simple`
  first runs the fact lookups the router already named (no model call), then at most 2 `cheap`
  tool-calling turns; `writer_simple` makes the `strong` `Answer` call with the Phase 3 inline
  citation check and one re-ask. Both nodes reuse the Phase 3 loop pieces, refactored out of
  `agent/loop.py` without behaviour change.
- **Deep path**: planner (1 `strong`), retriever (deterministic fact lookups and searches per
  step, then 1 `cheap` sufficiency judgment for all steps, up to 2 rewrites), analyst (1 call
  to write findings and calculation requests, then sandbox), verifier (deterministic checks
  plus 1 `strong` claim-support call), writer (1 `strong` call to the `Report` schema plus a
  deterministic report check and one re-ask).
- Typical deep run: 1 + 1 + 1–3 + 1 + 1 + 1 = 6–8 calls; one verifier round-trip adds about
  5. The 20-call limit covers two revisions only when retrieval needs no rewrites; the planner's
  estimate (R4) says so up front.

**Rationale**: Batching per stage, not per step, keeps model calls flat as plans grow, which
matters more than anything else on free quotas. The Phase 3 pieces keep simple questions as
good as they are now (SC-007).

**Alternatives considered**: One retriever call per plan step (up to 16 calls for 8 steps;
does not fit 20). A model-free router (saves a call, but cannot pull tickers, periods and
memory intent out of free text in two languages reliably). Re-approval after each verifier
revision (safer in theory, but the user already approved the scope; revisions are restricted
to that scope in code instead, R10).

## R3. Limits, deadlines and retries

**Decision**: `Budget` per mode in settings: simple 4 calls / 90 s, deep 20 calls / 300 s,
deep max 12 graph steps after approval (configurable). Usage (`model_calls`, `tool_calls`,
`sandbox_runs`, tokens, providers) lives in graph state, so it survives restarts. The deadline
is an absolute UTC timestamp in state (`deadline_at`): simple runs set it at start; deep runs
set it at approval (planning counts toward the call limit but not the clock). Every model call
goes through one guard that checks calls left and time left and wraps the call in
`asyncio.wait_for`. A node that hits a limit sets `limit_hit` and the conditional edges route to
`finalize_partial`, which builds a partial report from verified material only. Transient tool
failures (`SearchUnavailable`, `SandboxUnavailable`, HTTP timeouts to the sandbox) use the
node's LangGraph `RetryPolicy` (2 attempts, exponential backoff from 0.5 s, jittered); model
fallback stays inside the router. Structured outputs are validated with Pydantic and re-asked
once if a call is left.

**Rationale**: FR-012–FR-014 require limits in code. An absolute deadline is the only form that
stays correct across a restart.

**Alternatives considered**: LangGraph's `recursion_limit` alone (counts super-steps, not
model calls or time). A monotonic-clock deadline as in Phase 3 (lost on restart).

## R4. Plans: structure, estimate, edits

**Decision**: `Plan` = up to 8 `PlanStep`s (default, configurable): `id`, `kind`
(`facts` | `search` | `calculate`), `goal` (≤ 300 chars, in the question's language),
`tickers` (≤ 4), `periods` (≤ 8, `YYYY-Qn` or `YYYY-FY`), optional `metrics` (from
`facts.KNOWN_METRICS`). The estimate is computed in code, not by the model:
`router 1 + planner 1 + retriever (1 + rewrites_allowed) + analyst 1 + verifier 1 + writer 1`,
plus one more `planner + retriever + analyst + verifier + writer` per allowed revision, capped
at the budget, and shown as "about N, at most M". Edits come back as a full structured `Plan`
from the dialog; validation is deterministic and makes no model call: step count, field limits,
advice detector on every `goal`, the estimate fits the calls left. Unknown tickers are
accepted and reported as not covered by the step that uses them (spec edge case). A rejected
edit returns `plan_invalid` with per-field reasons and leaves the run waiting.

**Rationale**: Structured fields make edits checkable without a model call and keep the plan
an input the user owns.

**Alternatives considered**: Free-text plan edits re-parsed by the planner (a model call per
edit, and the user cannot see what was understood). A model-written estimate (not checkable).

## R5. Human-in-the-loop decisions

**Decision**: The `decision` node calls `interrupt({...})` with `kind` = `plan` or
`deep_quota_offer`. Before the interrupt, the run row gets `status = awaiting_approval`,
`plan`, `plan_expires_at = now + 24 h` (configurable) and the stream gets `awaiting_decision`;
the asyncio task then ends. `POST /v1/research/{id}/approve` accepts
`{"action": "approve" | "edit" | "cancel" | "simple", "plan"?: Plan}` (`simple` only for the
quota offer). The first decision wins via one conditional
`UPDATE runs SET status='running', decided_at=now() WHERE id=:id AND user_id=:user AND
status='awaiting_approval' AND plan_expires_at > now() RETURNING id`; a later or concurrent
decision gets `already_decided` (409), an expired plan `plan_expired` (410), a non-owner
`not_found`. The API then claims the run lease (R7) and resumes the graph with
`Command(resume=decision)`. `cancel` ends the run as `cancelled`.

**Rationale**: The DB update is the one atomic point that makes "first decision wins" (FR-005)
true across two API workers and two tabs.

**Alternatives considered**: Redis `SET NX` on a decision key (works, but the run row would
still need its own update; two sources of truth).

## R6. Quotas for two modes

**Decision**: Keep the Phase 3 key `quota:{user}:{day}` for simple questions and add
`quota:deep:{user}:{day}` (default limits 20 and 5, `QUOTA_DAILY_DEEP=5`). Every question
takes one simple unit at submission, as today. When the router says `deep`, `quota_gate`
reads the deep counter without taking: if it is used up, the run offers simple mode (R5,
`deep_quota_offer`) before any planner call. On `approve`/`edit`, one Lua script takes a deep
unit and refunds the simple unit taken at submission (on that submission's day key, never below
zero). If no deep unit is left at that moment (another tab used it), approval fails with
`quota_exhausted` and the plan stays open. Cancelled and expired plans keep their simple
unit, so FR-027 needs no extra step. `GET /v1/me` returns both quotas.

**Rationale**: The mode is unknown at submission; charging a simple unit first and swapping it
on approval keeps every path to exactly one unit (FR-023, FR-027) with one atomic script.

**Alternatives considered**: Charging deep at plan generation (a cancelled plan would cost a
deep unit). Charging nothing until approval (plans would be free to generate and drain
`strong` quota).

## R7. Resume after restart: leases and a supervisor

**Decision**: A run is executed only by the process holding `run:{id}:lease` in Redis
(`SET NX PX 30000`, renewed every 10 s, value = process id). Each API worker starts a
`RunSupervisor` task that, every 15 s and once at startup: (1) finds `runs` with
`status='running'` whose lease key is missing and claims them; (2) resumes each claimed run
with `graph.ainvoke(None, config)` from its last checkpoint and emits `run_resumed`
(`from_node`, `attempt`); (3) marks `awaiting_approval` runs past `plan_expires_at` as
`expired` and emits `run_expired`; (4) once a day deletes checkpoint threads of runs finished
more than 7 days ago. On SIGTERM, running tasks are cancelled and their leases deleted, so the
new container picks them up on its first sweep instead of waiting for expiry. If a renewal
fails (lease taken by someone else), the task stops at the next node boundary.
`mark_interrupted` is removed; the Phase 3 single-agent path (R15) keeps its own behaviour
when it is the configured engine.

Re-running the unfinished node is safe: calculations are pure, memory writes are idempotent
upserts, quota is taken by the API before resume (never inside the graph), and model calls that
had completed before the crash are response-cache hits. Every event carries `node` and
`attempt`, and the timeline replaces a node's events when a higher attempt arrives, so the
viewer does not see a step twice (FR-022). Redis stream ids stay monotonic across restarts
because Redis does not restart with the API.

**Rationale**: Covers FR-019–FR-023 with Redis, which is already there; the lease makes
"exactly one process" true when two uvicorn workers start at once.

**Alternatives considered**: Postgres advisory locks (held per connection; a pooled connection
would leak the lock across runs). Marking interrupted runs failed (Phase 3 behaviour; fails
US3).

## R8. Calculation sandbox

**Decision**: A new compose service `sandbox` (API image, command
`uvicorn sandbox.service:app`, `mem_limit: 128m`) is the only container with
`/var/run/docker.sock`. It sits on a new internal network `sandbox_net`
(`internal: true`) shared only with `api`, publishes no port, and exposes one endpoint:
`POST /run {"code", "inputs", "timeout_s"}` → `{"ok", "outputs", "stdout", "error",
"duration_ms", "timed_out", "oom"}`. For each call it creates a fresh container from the
pinned image `ghcr.io/omar9564/disclosly-sandbox:<tag>` over the Docker Engine API (`httpx`
with a Unix-socket transport; no Docker SDK) with a fixed `HostConfig`: `NetworkMode: none`,
`ReadonlyRootfs: true`, `Memory`/`MemorySwap` 512 MiB, `NanoCpus` 1e9, `PidsLimit` 64,
`CapDrop: ["ALL"]`, `SecurityOpt: ["no-new-privileges"]`, user `65534`, `/tmp` as a 16 MiB
`noexec` tmpfs, no mounts, no env except the payload. The payload (code + inputs, ≤ 64 KiB,
base64 JSON) is passed in one environment variable. The service waits up to 20 s, kills and
removes the container on timeout, reads at most 64 KiB of stdout, and allows 2 concurrent
executions (an `asyncio.Semaphore`; waiting counts toward the caller's deadline). Request
fields cannot change the image or `HostConfig`.

The sandbox image (`sandbox/Dockerfile`, `python:3.12-slim` + pinned `pandas` 3.0.x and
`numpy` 2.5.x, approved 2026-10-22) runs `python -I /runner/run.py`: it decodes the payload,
executes the code with `inputs` (a dict of name → `Decimal`-as-string values) in a fresh
namespace, and prints `{"outputs": {name: number}, "tables"?: …}` as JSON. Code must assign a
dict to `result`. pandas and numpy live only in this image, not in the app's dependencies.

**Rationale**: PROJECT_SPEC §4 and §13 ask for a no-network, read-only, resource-limited,
fresh container per run. Keeping the Docker socket in a tiny service that never runs user code
itself, with a fixed container spec, keeps the API and the worker (which parses untrusted
PDFs) away from root-equivalent access.

**Alternatives considered**: Subprocess with rlimits in a no-egress container (rejected by the
owner: no network namespace per run, not a fresh container). Mounting the socket into `api`
(any API bug becomes host root). `docker-socket-proxy` (another image, and it still allows
`containers/create` with any spec). gVisor `runsc` (stronger isolation, needs a host runtime
install; noted as Phase 5 hardening).

## R9. Analyst: findings and calculations

**Decision**: The analyst makes one model call returning `AnalystOutput`: `findings` (claims,
each with `cite_ids` and optional `calc` names) and `calculations` (each: `name`,
`description`, `inputs: [{name, cite_id, value?}]`, `code`). Input values are resolved in
code: a fact input takes the stored value (with its scale); a chunk input must give `value`, and
that number must appear in the cited chunk text (the Phase 3 number matcher). The model never
types a fact's number. All calculations of a round run in one sandbox execution. Each result
becomes a `Calculation` with cite id `k:<n>` (unique within the run) whose citation shows
inputs, their sources, the code and the output.

**Rationale**: FR-008 and FR-017. One execution per round keeps container starts to one or two
per run.

**Alternatives considered**: A small expression language evaluated in-process (no sandbox, but
PROJECT_SPEC §7 defines `run_python` with pandas/numpy and trends over many periods are easier
in pandas). One sandbox run per calculation (more container starts for no gain).

## R10. Verifier

**Decision**: Three checks, in order:

1. **Inputs** (code): every calculation input is re-resolved from its citation (fact value
   from the database; chunk number from the stored chunk text). A mismatch fails the
   calculation.
2. **Recompute** (sandbox): all calculations re-run in one fresh sandbox execution with the
   re-resolved inputs; every output must equal the recorded output within
   `max(1e-9 × |value|, 0.005)` (and, for percentages, 0.1 pp as in Phase 3).
3. **Claim support** (1 `strong` call): for each finding, "is this claim supported by the
   cited excerpts / calculations?" returning `[{finding_id, supported, reason}]`. Excerpts are
   wrapped as untrusted data.

Any failure records reasons, increments `verify_rounds` and routes back to the planner (max 2
rounds). The revised plan is validated in code to stay within the approved tickers, periods
and kinds; no second approval. After the last round, unsupported findings and failed
calculations are dropped, `confidence = low`, the report is `partial`, and `unverified` lists
what was dropped. The writer may only use verified findings and calculations; the final report
check (R11) rejects any number not traceable to them.

**Rationale**: PROJECT_SPEC §6.2 ("check each claim has a supporting source; recompute
numbers"). Numbers are checked in code; only textual support needs a model.

**Alternatives considered**: An LLM judge for numbers too (cannot recompute reliably).
Re-running the code with the analyst's inputs only (proves determinism, not correctness of the
inputs).

## R11. Report schema and citations

**Decision**: `Report` is a superset of the Phase 3 `Answer`: `language`, `status`
(`completed` | `partial` | `refused` | `no_evidence`), `summary`, `points`, plus `tables`
(`title`, `columns`, `rows` of cells `{text, cite_id?}`), optional `chart`
(`kind: line | bar`, `table` index, `x_column`, `series` column names; the values come from
the table, so FR-018 holds by construction), `confidence` (`high` | `medium` | `low`,
`note`), `unverified` (list of strings), `citations`, `disclaimer` (server-set). Simple runs
produce a `Report` with no tables or chart. A deterministic report check extends
`validate_answer`: every number in summary, points and table cells must be in a cited fact or
chunk or equal a verified calculation output; table cells with numbers must carry a `cite_id`;
the chart must reference existing numeric columns. `run_citations.cite_id` accepts `k:` (check
constraint `^[cfk]:.+`); a calculation citation stores the formula description as `excerpt`
and `{kind: "calculation", inputs: [cite_id], code, output}` as `source`.

**Rationale**: One schema for both modes keeps the web renderer and the stored row shape
single. FR-016–FR-018.

**Alternatives considered**: A separate deep-report table (two read paths for one page).
Charts as images from the sandbox (not themeable, not accessible, values not checkable).

## R12. Web: plan dialog, report view, chart, settings

**Decision**: New components `PlanDialog` (steps list with editable goal, tickers, periods,
kind; add/remove/reorder; estimate and remaining deep quota; approve/edit/cancel), `ReportView`
(summary, points, `ReportTable`, `ReportChart`, confidence note, unverified list),
`CalculationPopover` (inputs with their sources, formula, output), and a `/settings` page
(watchlist with company search, preferences, delete-all with confirmation). `ReportChart` is a
hand-written SVG line/bar chart (axes, legend, values on focus/hover, table fallback for
screen readers); no chart library (owner choice, 2026-10-22). Design goes through the
`impeccable` skill before code, as for earlier UI work. Strings in `tr.ts`/`en.ts`.

**Rationale**: Line and bar charts are a few hundred lines of tested SVG; a library adds bundle
weight for features we do not use.

**Alternatives considered**: Recharts (declined by the owner).

## R13. Memory

**Decision**: Tables `watchlist_items (user_id, company_id, added_at)` with PK
`(user_id, company_id)` and `user_preferences (user_id PK, answer_language, default_period_range,
updated_at)`; `default_period_range` ∈ `last_4q`, `last_8q`, `last_2fy`, `last_3fy`. Watchlist
capped at 50 per user (checked in the same transaction as the insert). The graph reads memory
once in `router` (code, not a model tool) and passes it to the planner as data. Memory changes
happen only in a `memory` step that executes `RouterDecision.memory_intent`, which is extracted
from the user's own question; retrieved text never reaches the router, so it cannot add
companies. Writes are idempotent upserts/deletes and emit `memory_updated`. Telemetry records
only counts (`watchlist_size`), never tickers or preferences. API (contracts/http-api.md):
`GET /v1/me/memory`, `PUT /v1/me/memory/preferences`, `POST /v1/me/memory/watchlist`,
`DELETE /v1/me/memory/watchlist/{ticker}`, `DELETE /v1/me/memory`, and
`GET /v1/companies?q=` for the settings search (corpus companies only). The privacy page gains
a paragraph on stored watchlist and preferences.

**Rationale**: PROJECT_SPEC §7 lists `get_user_memory`/`save_user_memory`; giving write access
to the model as a free tool would let injected text change memory (FR-015, FR-031).

**Alternatives considered**: A generic key/value memory table (no constraints on what is
stored; harder to validate and to show in the settings page).

## R14. Events and streaming

**Decision**: Events gain `node` and `attempt` fields. New types: `mode_selected`,
`plan_proposed`, `awaiting_decision` (`kind`, `plan`, `estimate`, `deep_remaining`,
`expires_at`), `decision_made`, `step_started`, `step_finished`, `calculation`,
`verification`, `memory_updated`, `run_resumed`, `run_expired`, `run_cancelled`. `final` now
carries a `Report`. `awaiting_decision` ends the SSE response (the run is paused; holding a
connection for up to 24 h wastes a worker slot); the client reconnects with `Last-Event-ID`
after it posts a decision. Phase 3 events keep their shapes.

**Rationale**: FR-024; one stream per run with resumable ids is already in place (ADR 0003 §5).

**Alternatives considered**: Keeping the stream open while waiting (pings for hours per
open tab).

## R15. Engines, rollout and the single-agent baseline

**Decision**: Setting `AGENT_ENGINE=graph|single` (default `graph`). `single` runs the
unchanged Phase 3 loop and is the eval baseline; it is also the rollback switch in production.
Per API worker, at most 3 graph runs execute at once (semaphore); further runs wait in order and
the wait counts toward their deadline.

**Rationale**: FR-037 needs both engines runnable on the same cases; a switch is cheaper than a
revert when free providers misbehave.

**Alternatives considered**: Deleting the Phase 3 loop (no baseline left to compare against).

## R16. Evals and tests

**Decision**:

- `evals/datasets/deep_golden.jsonl` (≥ 16 cases, TR and EN): multi-company comparisons,
  ratios, growth, trends with chart, watchlist questions (with a memory fixture), period-less
  questions (default range), router labels (`expected_mode`).
- `evals/datasets/verifier_seeded.jsonl` (≥ 20 drafts): wrong calculation outputs, wrong
  inputs, numbers with no source, unsupported claims; scored without model calls except the
  claim-support step, which is replayed from cassettes. SC-003 needs ≥ 90% rejected.
- Advice and injection sets gain deep-mode cases (plan text, memory-changing injections).
- Graph runs are recorded with `evals/record_graph.py` (live, owner's laptop) into
  `evals/cassettes/graph/`, including sandbox outputs, and replayed in CI without network or
  Docker. Cassette keys reuse `llm/cache_key.py`.
- `python -m evals.run compare --replay` scores `single` and `graph` on the shared cases
  (answers golden + deep golden) and writes `evals/reports/<date>-single-vs-multi.md`;
  `docs/evals/single-vs-multi.md` links the latest report and states weaknesses. The README
  eval table gains both rows.
- Restart tests (`tests/integration/agent/test_resume.py`): real Postgres and Redis, fake
  router counting calls; the run task is cancelled right after the checkpoint of each node
  (and inside a model call and a sandbox call), a new `RunService` + supervisor starts, and the
  test checks the final status, no repeated completed node, one quota unit, ordered event ids.
- Sandbox tests (`tests/integration/sandbox/`, marker `sandbox`, need Docker): escape
  attempts (socket connect, DNS, file reads of `/etc/shadow` and `/proc/1/environ`, writes
  to `/`, fork bomb, 1 GiB allocation, endless loop, env secrets) must fail or be killed within
  limits. CI runs them in a job that builds the sandbox image.

**Rationale**: Constitution II (every LLM feature ships with eval cases; CI replays cassettes).

**Alternatives considered**: Running the sandbox live in the eval gate (needs Docker in every
eval run and slows the gate; the escape tests cover the sandbox itself).

## R17. Observability

**Decision**: OTel spans `agent.node` (attributes: node, attempt, run_id, hashed user id,
model calls used), `sandbox.run` (duration, exit status, timed_out, oom, outputs count),
`agent.verify` (round, failures by type), `agent.resume` (from_node). Usage per node is stored
in `runs.usage.nodes`. The checkpoint (7 days) plus the trace is how a failed run is inspected
(FR-034); a replay tool is Phase 5.

**Rationale**: Constitution V.

**Alternatives considered**: LangGraph/LangSmith callbacks (sends data to a third party; off).

## R18. New error codes

**Decision**: `already_decided` (409), `plan_invalid` (422, with `details.reasons`),
`plan_expired` (410), `not_awaiting_decision` (409), `watchlist_full` (422),
`company_not_covered` (422), and run-level `sandbox_unavailable` and `interrupted_limit`
(a run resumed more than 3 times is failed to avoid restart loops). Turkish and English
messages in `api/errors.py`.

**Rationale**: Constitution V (stable codes); spec edge cases.

**Alternatives considered**: Reusing `validation_error` for plan edits (loses the per-step
reasons the dialog needs).
