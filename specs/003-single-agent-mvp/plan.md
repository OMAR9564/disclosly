# Implementation Plan: Single-Agent MVP

**Branch**: `003-single-agent-mvp` | **Date**: 2026-10-21 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/003-single-agent-mvp/spec.md`

## Summary

Deliver cited answers end to end on production for the owner and allowlisted test accounts. A new `llm/` package holds the `ModelRouter`:
ordered free providers per task class (`cheap`, `strong`), all spoken to over the
OpenAI-compatible chat API with `httpx`, cooldown on 429/5xx/timeout/empty output, per-provider
daily counters in Redis, and a two-layer response cache (Redis hot, Postgres durable). A new
`agent/` package runs one tool-calling loop (no LangGraph yet) over three tools that wrap the
Phase 2 retrieval layer: `search_documents`, `get_financial_facts`, `list_documents`. The loop
enforces 4 model calls and a 90 s deadline in code, validates the final answer against a
Pydantic `Answer` schema (one re-ask), and checks that every cited id was returned by a tool in
this run. Runs execute in the API process as asyncio tasks; events go to a Redis stream and are
served over SSE, so a reload replays the run. Auth is server-side OAuth (Google, GitHub) in the
API, a short-lived JWT in an HttpOnly cookie on `.disclosly.com`, and an owner-managed email
allowlist. Quotas and rate limits use atomic Redis counters. The web app gets sign-in, ask,
answer-with-citations, quota indicator and KVKK/terms/disclaimer pages, Turkish default.

## Technical Context

**Language/Version**: Python 3.12+ (mypy strict, Pydantic v2); TypeScript strict (Next.js 16,
React 19).

**Primary Dependencies**: existing (FastAPI, httpx, Pydantic, SQLAlchemy, Redis, Alembic,
OpenTelemetry API). **One new backend dependency, approved by owner 2026-10-21**: `PyJWT` for session
tokens (research §R4). No new frontend dependencies: SSE uses the browser `EventSource`,
Turnstile loads as Cloudflare's script tag.

**Storage**: PostgreSQL 16 (new tables: `users`, `beta_allowlist`, `runs`, `run_citations`,
`llm_cache`); Redis (provider cooldown and daily usage, cache hot layer, quota and rate-limit
counters, run event streams).

**Testing**: pytest + pytest-asyncio; router tests with an `httpx.MockTransport`; agent evals
replayed from LLM cassettes (`evals/cassettes/agent/`); integration tests against Postgres/Redis
service containers; Vitest for web components; one Playwright smoke test (ask → answer →
citation) against a mocked API.

**Target Platform**: API on the VPS (Docker Compose, `api` 512 MB); web on Vercel.

**Project Type**: Web service + web app.

**Performance Goals**: first SSE event < 3 s, final answer < 30 s for 95% of simple questions
(SC-005); cache hit returns with zero provider calls.

**Constraints**: $0 budget (free tiers only); ≤ 4 model calls and ≤ 90 s per run; `api`
container 512 MB with 2 uvicorn workers; no live model calls in CI; no emails or tokens in
logs.

**Scale/Scope**: owner + a few test accounts now, sized for up to ~50 users, default 20 questions/user/day → at most ~1,000
runs/day, ~4,000 model calls/day before cache.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | How this plan complies |
|---|---|---|
| I. Spec-First | Pass | Spec complete, FR-021 resolved; plan and contracts trace to FR/SC IDs. |
| II. Test-First | Pass | Unit tests for router, cache, quota, auth, agent loop and citation check; integration tests for SSE and quota atomicity; agent eval subset (numeric, refusal, injection; TR + EN) replayed from cassettes in CI; coverage gate extended to `llm`, `agent`, `tools`. |
| III. Simplicity | Pass | Plain async loop instead of LangGraph until Phase 4; one HTTP client shape for all providers; runs as asyncio tasks in the API, no new worker queue; Redis streams for events. One new dependency (PyJWT), justified in §R4. |
| IV. Security & No Secrets | Pass | Provider keys, OAuth client secrets, JWT secret and Turnstile secret via env only; HttpOnly + Secure + SameSite=Lax cookie; tool output wrapped in delimiters; cassettes scrubbed of auth headers; BYOK out of scope. |
| V. Observability | Pass | OTel span per run, model call and tool call with GenAI attributes (provider, model, tokens, latency, cache hit, would-have-cost); user id hashed; new stable error codes (`unauthorized`, `beta_closed`, `quota_exhausted`, `rate_limited`, `providers_unavailable`, `bot_check_failed`). |
| Quality gates | Pass | Ruff/mypy paths extended; eval gate adds citation coverage, refusal and injection pass rate. |

**Gate condition**: met — PyJWT approved by the owner on 2026-10-21.

Post-design re-check (after Phase 1): unchanged, all pass.

## Project Structure

### Documentation (this feature)

```text
specs/003-single-agent-mvp/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md        # /v1/auth, /v1/research, /v1/me endpoints + error codes
│   ├── sse-events.md      # event types and payloads on the run stream
│   ├── model-router.md    # router interface, providers.yaml, cache key
│   └── agent-answer.md    # tools, Answer schema, citation rules, eval cassette format
└── tasks.md               # /speckit-tasks
```

### Source Code (repository root)

```text
llm/
├── config.py              # providers.yaml loader (model ids, quotas, prices per class)
├── providers.yaml
├── client.py              # OpenAI-compatible chat call over httpx, tool-call normalisation
├── router.py              # ModelRouter: fallback, cooldown, daily usage, telemetry
├── cache_key.py           # canonical request hash (shared by cache and cassettes)
├── cache.py               # response cache (Redis + Postgres)
└── types.py               # ChatRequest, ChatResponse, ToolCall, TaskClass
tools/
├── registry.py            # tool schemas + dispatch, untrusted-data wrapping
├── documents.py           # search_documents, list_documents (wrap retrieval/)
└── facts.py               # get_financial_facts
agent/
├── prompts/               # system prompts (tr, en)
├── loop.py                # single tool-calling loop with budget + deadline
├── answer.py              # Answer/Citation models, validation, citation check
├── advice.py              # advice-seeking detector (keywords + cheap-model fallback)
└── runs.py                # run lifecycle, event publishing, persistence
api/
├── auth/                  # oauth.py (Google, GitHub), session.py (JWT cookie), allowlist.py
├── guards/                # quota.py, ratelimit.py, turnstile.py
├── routes/                # research.py (start, stream, get), me.py, auth.py
└── migrations/versions/0003_users_runs_cache.py
web/src/
├── app/                   # page.tsx (ask), runs/[id]/, signin/, privacy/, terms/, disclaimer/
├── components/            # AskBox, Timeline, AnswerView, CitationPopover, QuotaBadge
├── lib/                   # api.ts, stream.ts (EventSource)
└── i18n/                  # tr.ts, en.ts additions
evals/
├── datasets/answers_golden.jsonl, advice_refusal.jsonl, injection.jsonl   # seed subsets
├── cassettes/agent/       # recorded provider responses
└── agent.py               # replay harness + metrics
tests/unit/{llm,agent,tools,api}/  tests/integration/{api,agent}/
```

**Structure Decision**: Follow PROJECT_SPEC §18 (`llm/`, `agent/`, `tools/` top-level
packages). Auth and guards live under `api/` because only the HTTP layer uses them.

## Complexity Tracking

No constitution violations. One new dependency (PyJWT), approved 2026-10-21, see research §R4.
