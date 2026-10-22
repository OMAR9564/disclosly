# Implementation Plan: Multi-Agent Research

**Branch**: `004-multi-agent-graph` | **Date**: 2026-10-22 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/004-multi-agent-graph/spec.md`

## Summary

Replace the Phase 3 single loop with a LangGraph graph (PROJECT_SPEC §6) persisted by the
LangGraph Postgres checkpointer, keeping the loop as a switchable baseline
(`AGENT_ENGINE=single`). A router node picks simple or deep mode. Simple questions take
Router → Retriever → Writer, built from the Phase 3 loop pieces, within 4 calls and 90 s. Deep
questions get a structured plan with an estimate computed in code; the graph pauses with
`interrupt()` until the owner of the run approves, edits or cancels it (first decision wins
through one conditional update). After approval, the retriever batches searches and fact
lookups. The analyst writes findings and calculation requests, and the calculations run in a
fresh Docker container through a new `sandbox` service, the only holder of the Docker socket.
The verifier re-resolves inputs, recomputes outputs and checks claim support, sending failures
back to the planner at most twice. The writer then produces a `Report` with tables, an SVG
chart, calculation citations and a confidence note. Runs survive restarts: Redis leases and a
supervisor task in each API worker resume interrupted runs from the last checkpoint, and the
same supervisor expires stale plans. Memory (watchlist, preferences) gets two tables, a
settings page and an API; only the user's own question can change it. Evals add a deep golden
set, a seeded verifier set, restart tests, sandbox escape tests and a dated single-vs-multi
comparison.

## Technical Context

**Language/Version**: Python 3.12+ (mypy strict, Pydantic v2); TypeScript strict (Next.js 16,
React 19).

**Primary Dependencies**: existing (FastAPI, httpx, Pydantic, SQLAlchemy, psycopg, Redis, arq,
Alembic, OpenTelemetry API, PyJWT). **New, approved by the owner on 2026-10-22**: `langgraph`
1.2.x and `langgraph-checkpoint-postgres` 3.2.x (bringing `langchain-core`, `langsmith`,
`psycopg-pool`, `orjson`, `ormsgpack`, `xxhash`), research §R1. Sandbox image only (not app
dependencies): `pandas` 3.0.x, `numpy` 2.5.x on `python:3.12-slim`, research §R8. No new
frontend dependencies (SVG chart, research §R12).

**Storage**: PostgreSQL 16: `runs` and `run_citations` changed, new `watchlist_items` and
`user_preferences` (migration 0004); LangGraph checkpoint tables in schema `agent_graph`.
Redis: deep quota counters, run leases, supervisor lock, existing event streams.

**Testing**: pytest + pytest-asyncio; graph node unit tests with a fake router and fake
sandbox; integration tests with Postgres/Redis service containers (resume, approval races,
quota swap, memory API); `sandbox`-marked tests against the real sandbox image (Docker in CI);
graph evals replayed from cassettes (`evals/cassettes/graph/`); Vitest for `PlanDialog`,
`ReportView`, `ReportChart`, settings page; one Playwright smoke test (deep question → plan →
approve → report) against a mocked API.

**Target Platform**: API and sandbox service on the VPS (Docker Compose); sandbox containers
started on the same Docker host; web on Vercel.

**Project Type**: Web service + web app.

**Performance Goals**: plan shown within 15 s for 95% of deep questions; deep report within 5
minutes of approval for 95%; simple answers within 30 s for 95% (SC-006, SC-007); sandbox
batch under 3 s for typical calculations.

**Constraints**: $0 budget; simple ≤ 4 calls / 90 s, deep ≤ 20 calls / 300 s after approval
(code-enforced); sandbox 512 MiB, 1 CPU, 20 s, no network, 2 at a time; `api` 512 MB with 2
uvicorn workers (LangGraph import overhead measured, research §R1); no live model calls or
Docker in the CI eval gate; no memory values, emails or keys in logs and traces.

**Scale/Scope**: owner + allowlisted test accounts (sized for ~50 users): ≤ 20 simple + 5 deep
per user per day → ≤ 250 deep runs/day worst case, ~5,000 model calls/day before cache.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | How this plan complies |
|---|---|---|
| I. Spec-First | Pass | Spec complete; the one clarification (memory UI) was answered by the owner (option A, 2026-10-22). Contracts and data model trace to FR/SC IDs. |
| II. Test-First | Pass | Unit tests per node, plan validation, quota swap, report checks, lease logic; integration tests for resume at every node boundary, approval races and memory API; sandbox escape suite; graph eval subset (deep, verifier seeded, advice, injection; TR + EN) replayed from cassettes; coverage gate extended to `sandbox`. |
| III. Simplicity | Pass, with justified additions | Two new dependencies and one new service, all approved by the owner on 2026-10-22 and recorded in ADR 0004. Batched stages instead of per-step calls; no LangChain model wrappers; no Docker SDK (httpx over the Unix socket); SVG chart instead of a library; Phase 3 loop reused rather than rewritten. See Complexity Tracking. |
| IV. Security & No Secrets | Pass | The Docker socket is mounted only into `sandbox`, which never executes code itself and has a fixed container spec; sandbox containers get no network, no env except the payload, read-only root, dropped capabilities. Memory changes only from the user's own question; memory and calculation code never reach router/planner prompts as instructions. LangSmith tracing refused at startup. Cassettes scrubbed as in Phase 3. |
| V. Observability | Pass | OTel spans per node, sandbox run, verify round and resume; usage per node in `runs.usage`; new stable error codes with TR/EN messages; memory values never logged. |
| Quality gates | Pass | Ruff/mypy paths add `sandbox`; graph eval joins the CI eval gate; a CI job builds the sandbox image and runs the `sandbox` suite; deploy workflow builds and pulls the sandbox image. |

**Gate condition**: met: dependencies and the sandbox service approved by the owner on
2026-10-22.

Post-design re-check (after Phase 1): unchanged, all pass. The Docker socket mount stays the
main risk and is listed in Complexity Tracking with its mitigations.

## Project Structure

### Documentation (this feature)

```text
specs/004-multi-agent-graph/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md       # approve endpoint, quotas, memory, companies, error codes
│   ├── sse-events.md     # new events, node/attempt fields, pause semantics
│   ├── report.md         # Report schema, code-checked rules, k: citations
│   ├── agent-graph.md    # nodes, allowed tools, budgets, settings
│   └── sandbox.md        # sandbox service API, container spec, escape cases
├── checklists/requirements.md
└── tasks.md              # /speckit-tasks
```

### Source Code (repository root)

```text
agent/
├── loop.py               # Phase 3 loop, split into research/answer helpers (behaviour unchanged)
├── report.py             # Report schema + code checks (extends answer.py rules)
├── plan.py               # Plan/PlanStep models, validation, estimate
├── budget.py             # Budget per mode, call/deadline guard
├── memory.py             # MemorySnapshot, read/write helpers (idempotent)
├── supervisor.py         # leases, resume sweep, plan expiry, checkpoint retention
├── runs.py               # RunService: engine switch, decisions, events with node/attempt
├── graph/
│   ├── state.py          # ResearchState + reducers
│   ├── build.py          # StateGraph wiring, edges, retry policies, checkpointer setup
│   └── nodes/            # router, memory, quota_gate, planner, decision, retriever,
│                         # retriever_simple, analyst, verifier, writer, writer_simple,
│                         # finalize_partial
└── prompts/              # per node, tr + en
tools/
├── registry.py           # per-node tool sets, schema version 2 for graph
├── python.py             # run_python client for the sandbox service
└── memory.py             # get_user_memory (read only)
sandbox/
├── service.py            # POST /run; Docker Engine API over the Unix socket
├── docker.py             # create/start/wait/kill/remove with the fixed HostConfig
├── runner/run.py         # runs inside the sandbox image
└── Dockerfile            # sandbox image (pandas, numpy)
api/
├── routes/research.py    # + POST /v1/research/{id}/approve
├── routes/memory.py      # /v1/me/memory*
├── routes/companies.py   # GET /v1/companies
├── guards/quota.py       # deep quota + swap script
├── errors.py             # new codes
└── migrations/versions/0004_graph_runs_memory.py
web/src/
├── app/settings/         # watchlist + preferences page
├── components/           # PlanDialog, ReportView, ReportTable, ReportChart (SVG),
│                         # CalculationPopover, WatchlistEditor; Timeline/RunView updated
├── lib/                  # api.ts (approve, memory, companies), runState.ts (node/attempt)
└── i18n/                 # tr.ts, en.ts additions; privacy content update
evals/
├── datasets/deep_golden.jsonl, verifier_seeded.jsonl (+ deep cases in advice/injection)
├── cassettes/graph/
├── graph.py, record_graph.py, compare.py
└── reports/<date>-single-vs-multi.md
infra/
├── docker-compose.yml    # sandbox service, sandbox_net, new env vars
└── docker-compose.dev.yml
.github/workflows/        # ci.yml: sandbox image build + sandbox tests; deploy.yml: build/pull image
docs/
├── adr/0004-multi-agent-graph.md
└── evals/single-vs-multi.md
tests/unit/{agent,agent/graph,sandbox,tools,api}/  tests/integration/{agent,api,sandbox}/
```

**Structure Decision**: Follow PROJECT_SPEC §18. The graph lives under `agent/graph/` next to
the Phase 3 loop it reuses; `sandbox/` is a top-level package as listed in §18 and ships in the
API image (service) plus its own image (runner).

## Complexity Tracking

| Addition | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| `langgraph` + `langgraph-checkpoint-postgres` (~9 transitive packages) | HITL interrupt and checkpoint resume are the phase acceptance criteria; PROJECT_SPEC §4 fixes LangGraph | Own state machine: owner chose LangGraph; would mean writing and proving our own interrupt/resume semantics |
| New `sandbox` service with the Docker socket | PROJECT_SPEC §4/§13 require a fresh no-network container per calculation | Socket in `api` (any API bug becomes host root); subprocess sandbox (no per-run network namespace; owner declined) |
| Redis leases + supervisor loop | "Exactly one process resumes a run" (FR-021) with two uvicorn workers | Startup-only resume (two workers would both resume the same run) |
| Second quota counter with an atomic swap script | Separate simple/deep quotas where a question's mode is unknown at submission (FR-027) | Charging at plan generation (cancelled plans would cost deep quota) |
