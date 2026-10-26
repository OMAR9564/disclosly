# ADR 0004: Multi-agent research graph

- Status: accepted
- Date: 2026-10-26
- Source: `specs/004-multi-agent-graph/research.md` (§R1–R18), contracts `sandbox.md`,
  `research-report.md`, `sse-events.md`, `http-api.md`

## Context

Phase 3 answers one question with one agent loop of at most four model calls. It cannot
compare several companies over several periods, it cannot compute a ratio it can show is
right, and a deploy fails every running run. Phase 4 adds deep research: the user approves a
plan, the system gathers facts, computes derived numbers in isolation, checks its own draft
and writes a report with tables and a chart. All of it must stay on free model tiers and one
2 vCPU / 4 GB server.

## Decision

### LangGraph with a Postgres checkpointer

The research flow is a LangGraph state graph: router → memory → (simple path:
retriever_simple → writer_simple) or (quota_gate → planner → decision → retriever → analyst →
verifier → writer), with `finalize_partial` as the exit for limits and errors. State is
checkpointed after every node (`durability="sync"`) by `AsyncPostgresSaver` in its own schema
`agent_graph`, so Alembic keeps owning `public`. Checkpoint threads are kept for
`CHECKPOINT_RETENTION_DAYS` (7) after a run ends, then deleted by the supervisor.

### Batched stages and the call budget

Each stage makes one model call for all plan steps together (one sufficiency check, one
analyst call, one claim-support call) instead of one call per step. A deep run is capped at 20
model calls, 300 s, 12 plan steps, 24 tool calls and 4 sandbox runs (`AGENT_DEEP_*`); the
simple path keeps Phase 3's 4 calls. Hitting a cap ends the run with a partial report that
says which cap was hit.

### Plan approval and the quota swap

The planner's plan pauses the graph (`interrupt`) until the user approves, edits or cancels
it. Edits are validated against the same caps. The deep quota (`QUOTA_DAILY_DEEP`, 5 a day) is
read by `quota_gate` but only taken when the plan is approved; with none left the run offers
simple mode before any planner call. A plan not decided within its window expires.

### Redis leases and the supervisor

A graph run executes only in the process that holds `run:{id}:lease` (`SET NX PX 30000`,
renewed every 10 s). Each API process runs a supervisor that, at startup and every 15 s,
claims `running` graph runs without a lease and resumes them from their last checkpoint. A run
resumed more than 3 times fails with `interrupted_limit`, so a crash loop cannot spin
forever. Under the lock `supervisor:sweep` the supervisor also expires plans and prunes old
checkpoints.

### Sandbox service

Calculations run as Python in a throwaway container. The `sandbox` service is the only
container with the Docker socket; `api` reaches it on the internal `sandbox_net` network
(`POST /run`). The service picks the image at startup and fixes the container spec: no
network, read-only root, 512 MiB with no swap, 1 CPU, 64 pids, all capabilities dropped,
`no-new-privileges`, user 65534, a 16 MiB `noexec` tmpfs, at most 20 s. Request fields
cannot change any of it. Inputs are passed as decimals tied to cited facts, never fetched by
the code.

### Verifier

The verifier recomputes every calculation in the sandbox, checks that each input equals the
cited fact (`input_mismatch`) and that the output matches (`recompute_mismatch`), and asks a
strong model whether each finding is supported by its cited evidence (`unsupported_claim`).
A failed round sends the run back to the planner within the approved scope; after
`AGENT_VERIFY_MAX_ROUNDS` (2) failed rounds the failing findings are dropped and listed as
unverified. A derived number in a report must come from a verified calculation (FR-008); the
simple path is not allowed to state one at all.

### Engine switch

`AGENT_ENGINE` selects `single` (Phase 3 loop) or `graph` for new runs. Both stay in the code:
the single agent is the baseline for the comparison in `docs/evals/single-vs-multi.md`, and
switching back is one environment change.

## Consequences

- A deploy no longer fails graph runs: the new process resumes them from the last checkpoint
  (ADR 0003's consequence is amended for `AGENT_ENGINE=graph`).
- Deep runs cost more: 4.3 model calls per run on average in the eval set against 2.6 for the
  single agent, up to 12 in one run. The deep quota exists because of this.
- The server runs one more long-lived container (`sandbox`, 128 MB) plus one short-lived
  container per calculation batch (at most 2 at once, 512 MiB each).
- The Docker socket is mounted into `sandbox`. That container runs no user code and has no
  route to the internet, but a flaw in it would reach the host's Docker daemon.
- Checkpoints add Postgres writes after every node, and storage for 7 days of threads.

## Alternatives considered

- One agent with more tools and a larger budget: no plan the user can check, and no place to
  verify numbers before the user sees them.
- Celery or another queue for runs: a new infrastructure component; leases in the Redis we
  already run were enough.
- Running calculations in-process with a restricted Python: escape-prone; containers give
  kernel-level limits.
- LangSmith tracing: sends run data to a third party.
