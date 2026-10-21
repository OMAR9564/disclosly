---

description: "Task list for Phase 3 — Single-Agent MVP"
---

# Tasks: Phase 3 — Single-Agent MVP

**Input**: Design documents from `specs/003-single-agent-mvp/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: Required by the constitution (Principle II). Every task ships with the tests named in it; coverage stays ≥ 80%. LLM-dependent tasks add eval cases replayed from cassettes; CI never calls a live provider.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1–US5 from [spec.md](./spec.md)

## Path Conventions

Backend packages at repo root (`api/`, `llm/`, `agent/`, `tools/`, `evals/`), tests under `tests/unit/` and `tests/integration/`, web app under `web/src/`.

---

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 Add the approved dependency `PyJWT` (research §R4, approved 2026-10-21) to `pyproject.toml` and `uv.lock`. Test: `tests/unit/test_package.py` imports `jwt`
- [X] T002 [P] Create packages `llm/`, `agent/`, `agent/prompts/`, `tools/`, `api/auth/`, `api/guards/`, `api/routes/` with `__init__.py`, and test folders `tests/unit/{llm,agent,tools,api}/`, `tests/integration/{api,agent}/`, `evals/cassettes/agent/`. Extend `pyproject.toml`: mypy `packages` and pytest `--cov` add `llm`, `agent`, `tools`
- [X] T003 [P] Add new variables to `.env.example` with placeholders only: `GEMINI_API_KEY`, `GROQ_API_KEY`, `GITHUB_MODELS_TOKEN`, `OPENROUTER_API_KEY`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_AI_TOKEN`, `LLM_PROVIDERS_FILE=llm/providers.yaml`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `JWT_SECRET=CHANGE_ME`, `SESSION_COOKIE_DOMAIN=.disclosly.com`, `WEB_ORIGIN=http://localhost:3000`, `TURNSTILE_SECRET_KEY` (Cloudflare test secret), `NEXT_PUBLIC_TURNSTILE_SITE_KEY` (Cloudflare test site key), `QUOTA_DAILY_QUESTIONS=20`, `RATE_LIMIT_USER_PER_MIN=30`, `RATE_LIMIT_IP_PER_MIN=60`, `AGENT_MAX_MODEL_CALLS=4`, `AGENT_DEADLINE_SECONDS=90`, `AGENT_MAX_TOOL_CALLS=6`, `OTEL_EXPORTER_OTLP_ENDPOINT=` (empty = no-op)

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ CRITICAL**: no user story work starts before this phase is complete.

- [X] T004 Extend `api/config.py` `Settings` with the T003 backend variables (all secrets `SecretStr`; provider keys optional; `JWT_SECRET` required outside `development`/`test`, min 32 chars). Tests in `tests/unit/api/test_config.py` cover each rule and that error messages never include values
- [X] T005 Create migration `api/migrations/versions/0003_users_runs_cache.py` and SQLAlchemy tables in `api/schema.py` exactly as [data-model.md](./data-model.md): `users` (`email` "unique", "lower-cased"; `display_name` "≤ 200 chars, any Unicode"; `auth_provider` enum `google`/`github`; unique `(auth_provider, provider_subject)`; `language` enum `tr`/`en` "default `tr`"), `beta_allowlist` (`email` PK "lower-cased"), `runs` (`question` "3–1,000 chars"; `status` enum `running`/`completed`/`partial`/`refused`/`no_evidence`/`failed`; `answer` jsonb nullable; `usage` jsonb; `error_code` nullable), `run_citations` (PK `(run_id, cite_id)`; `excerpt` "≤ 1,500 chars"; `source` jsonb), `llm_cache` (`key char(64)` PK, `hits`, `last_hit_at`). Downgrade drops all. Extend `tests/integration/test_migrations.py` with the round-trip and constraints
- [X] T006 [P] Add new error codes to `api/errors.py` per [contracts/http-api.md](./contracts/http-api.md): `unauthorized` 401, `beta_closed` 403, `bot_check_failed` 403, `quota_exhausted` 429, `rate_limited` 429 (with `Retry-After`), `providers_unavailable` 503; localized messages (TR/EN from `Accept-Language`). Tests in `tests/unit/api/test_errors.py`
- [X] T007 [P] Create `llm/types.py` (`TaskClass`, `ChatMessage`, `ToolSchema`, `ToolCall`, `ChatRequest`, `ChatResponse`, `ProvidersUnavailable`) and `llm/config.py` loading `llm/providers.yaml` per [contracts/model-router.md](./contracts/model-router.md) (unknown provider in a class list is an error; provider with unset `api_key_env` is skipped). Add `llm/providers.yaml` with the five providers and placeholder quotas. Tests in `tests/unit/llm/test_config.py`
- [X] T008 Create `llm/client.py`: one OpenAI-compatible chat call over `httpx.AsyncClient` (15 s timeout), tool-call normalisation (arguments parsed to dicts; unparsable → `InvalidResponse`), JSON-schema output mode for providers with `native_tools: false`, token usage extraction. Tests in `tests/unit/llm/test_client.py` with `httpx.MockTransport` fixtures for each provider's response shape
- [X] T009 Create `llm/cache_key.py` (sha256 of canonical JSON: NFC, trimmed and collapsed whitespace; model, temperature, max_tokens, tool_schema_version, response_schema; tests in `tests/unit/llm/test_cache_key.py`), then `llm/router.py` `ModelRouter.chat(request, task_class=...)` with the ordered provider walk only (no cooldown/cache yet; those are US2) and a `FakeRouter` replaying cassettes in `llm/testing.py` (keyed by `llm/cache_key.py`; unknown key raises so tests never silently go live). Tests in `tests/unit/llm/test_router_basic.py`
- [X] T010 [P] Create `api/auth/session.py`: issue/verify HS256 JWT (15 min, claims `sub`, `exp`, `iat`) with PyJWT; FastAPI dependency `current_user` reading the `session` cookie, raising `unauthorized`; a test-only helper to mint a session for integration tests. Tests in `tests/unit/api/test_session.py` (expired, tampered, missing)
- [X] T011 [P] Add a telemetry helper `llm/telemetry.py`: OTel span `gen_ai.chat` with attributes from research §R11 and hashed `user_id`; span helpers for runs and tool calls in `agent/telemetry.py`; email masking function in `api/logging.py`. Tests in `tests/unit/llm/test_telemetry.py` with an in-memory span exporter

**Checkpoint**: router can call a provider, sessions verify, schema exists.

---

## Phase 3: User Story 1 — Ask a question and get a cited answer (Priority: P1) 🎯 MVP

**Goal**: a signed-in user asks in TR or EN and gets a streamed answer where every number links to a source excerpt, with the disclaimer.

**Independent Test**: with a minted test session and `FakeRouter` cassettes, ask the seed questions; each stream emits progress within 3 s, ends with `final`, every number has a citation whose excerpt contains it, disclaimer present.

- [ ] T012 [P] [US1] Implement `tools/registry.py` (schemas, `tool_schema_version = "1"`, dispatch, wrapping in `<untrusted_document cite_id="…">…</untrusted_document>` with inner delimiter escaping) and `tools/documents.py` (`search_documents` with `k` "≤ 8", `list_documents`) wrapping `retrieval/search.py`, each chunk item with `cite_id = "c:<chunk_id>"`, per [contracts/agent-answer.md](./contracts/agent-answer.md). Tests in `tests/unit/tools/test_documents.py` (escaping of a chunk containing `</untrusted_document>`)
- [ ] T013 [P] [US1] Implement `tools/facts.py` `get_financial_facts(ticker, metrics[], periods[])` over `financial_facts`, items with `cite_id = "f:<fact_id>"`, value, unit, currency, period. Integration tests in `tests/integration/agent/test_facts_tool.py`
- [ ] T014 [P] [US1] Implement `agent/answer.py`: Pydantic `Answer`/`Point`/`CitationRef` per [contracts/agent-answer.md](./contracts/agent-answer.md); `validate_answer(answer, seen_items, language)` enforcing rules 1–4 (unknown `cite_id`; numbers not found in cited items with TR `1.234,5` / EN `1,234.5` normalisation; derived percentages within 0.1 pp of two cited facts; language match); server overwrites `disclaimer` with "Yatırım tavsiyesi değildir / Not investment advice". Tests in `tests/unit/agent/test_answer.py` (table-driven, TR and EN number formats)
- [ ] T015 [P] [US1] Implement `agent/advice.py`: TR + EN keyword/regex detector for buy/sell/hold, price target, portfolio advice ("almalı mıyım", "hedef fiyat", "should I buy", "price target", …), and language detection for the question (`tr` if Turkish-specific letters or stopwords, else `en`). Tests in `tests/unit/agent/test_advice.py` (≥ 20 positive, ≥ 20 negative cases)
- [ ] T016 [P] [US1] Write system prompts `agent/prompts/system_tr.md` and `agent/prompts/system_en.md`: tool use, cite every number, untrusted-document rule, no advice, answer JSON schema, "say no evidence instead of guessing". Test: `tests/unit/agent/test_prompts.py` checks both load and contain the untrusted-data rule and the schema
- [ ] T017 [US1] Implement `agent/loop.py` `run_agent(question, router, tools, budget, emit)`: `cheap` calls with tools; the last allowed call is forced to produce `Answer` with `strong`; budget `AGENT_MAX_MODEL_CALLS` (4), `AGENT_MAX_TOOL_CALLS` (6), `AGENT_DEADLINE_SECONDS` (90) enforced in code; schema/validation failure → one re-ask with errors only if a model call is left (the re-ask counts toward the limit of 4), otherwise or on second failure `partial` with validated points only; advice question → `refused` answer with information only; no evidence → `no_evidence` status; emits `tool_called`/`tool_result`/`model_called` events per [contracts/sse-events.md](./contracts/sse-events.md). Tests in `tests/unit/agent/test_loop.py` with `FakeRouter`: call limit, deadline (fake clock), re-ask, partial, refused, company not in corpus → `no_evidence`, injection chunk not followed
- [ ] T018 [US1] Implement `agent/runs.py`: create `runs` row, start the loop as an asyncio task, `XADD` events to `run:{id}:events` (24 h TTL after finish), persist answer, `usage` (including `time_to_first_event_ms` and `total_ms`, also set as run span attributes) and `run_citations` (`excerpt` truncated to 1,500 chars); startup hook marks stale `running` rows `failed` with `error_code = "interrupted"`. Integration tests in `tests/integration/agent/test_runs.py`
- [ ] T019 [US1] Implement `api/routes/research.py` per [contracts/http-api.md](./contracts/http-api.md): `POST /v1/research` (body `question` 3–1000 chars, requires `current_user`; returns 202 `run_id`), `GET /v1/research/{run_id}/stream` (SSE via `StreamingResponse`, `Last-Event-ID` resume, `: ping` every 15 s, closes after `final`/`error`), `GET /v1/research/{run_id}` (owner only, else `not_found`). Register in `api/main.py`; CORS allows `WEB_ORIGIN` with credentials and POST. Integration tests in `tests/integration/api/test_research.py`: full stream, reload replay, other user gets 404
- [ ] T020 [P] [US1] Seed eval sets and cassettes: `evals/datasets/answers_golden.jsonl` (20 cases, 10 TR + 10 EN, plus 3 cases for a company not in the corpus expecting `no_evidence` with a "not covered" note, `expected_numbers` with tolerance, `expected_cite_ids`), `evals/datasets/advice_refusal.jsonl` (20 cases TR + EN), `evals/datasets/injection.jsonl` (15 cases with `poisoned_chunk`, `forbidden_behaviour`); recorder `evals/record_agent.py` that writes `evals/cassettes/agent/<case_id>.json` with auth headers stripped. Test: `tests/unit/evals/test_agent_datasets.py` validates the JSONL schemas. (owner action) Running the recorder live needs provider keys: the agent writes the datasets and the recorder, the owner runs it once and commits the scrubbed cassettes
- [ ] T021 [US1] Implement `evals/agent.py` replay harness and wire `python -m evals.run agent --replay`: metrics citation coverage, numeric match rate, refusal rate, injection pass rate, model calls per run; report to `evals/reports/`; baseline in `evals/baselines/agent.json`; CI gate fails if citation coverage < 100%, refusal < 100%, injection < 100%, or numeric match drops > 5 pp vs baseline. Add the step to `.github/workflows/ci.yml`. Tests in `tests/unit/evals/test_agent_harness.py`
- [ ] T022 [P] [US1] Web API client `web/src/lib/api.ts` (`credentials: "include"`, typed `Run`, `Answer`, `Citation`, `QuotaStatus`, error codes) and `web/src/lib/stream.ts` (`EventSource` with `withCredentials`, typed events, reconnect). Vitest tests in `web/tests/lib/stream.test.ts` with a mock EventSource
- [ ] T023 [US1] Web ask flow: `web/src/components/AskBox.tsx`, `Timeline.tsx` (renders `tool_called`/`model_called` summaries), `AnswerView.tsx` (summary with `[n]` markers, points, disclaimer, partial/refused/no-evidence banners), `CitationPopover.tsx` (excerpt, document, ticker, period, section/page, source link); pages `web/src/app/page.tsx` (ask) and `web/src/app/runs/[id]/page.tsx` (reload-safe view). TR/EN strings in `web/src/i18n/{tr,en}.ts`. Vitest tests per component in `web/tests/components/`
- [ ] T024 [US1] Playwright smoke test `web/tests/e2e/ask.spec.ts` against a mocked API (route interception): ask → timeline → answer → click citation → excerpt visible → disclaimer visible

**Checkpoint**: US1 works end to end with a test session and recorded responses.

---

## Phase 4: User Story 2 — Answers keep working when a free provider fails (Priority: P1)

**Goal**: automatic fallback, cooldown, quota-aware skipping and response cache, visible in telemetry.

**Independent Test**: force the first provider to fail in the recorded test; the run completes on the next provider, cooldown is respected, `fallback_from` is in the trace; an identical repeat makes zero provider calls.

- [ ] T025 [US2] Add cooldown and failure classification to `llm/router.py` exactly per the table in [contracts/model-router.md](./contracts/model-router.md) (429 → `Retry-After` or 60 s; 5xx/timeout/connect → 60 s; empty or unparsable → 30 s; 400/401/403 → 1 h + config-error log), Redis key `llm:cooldown:{provider}`; record `fallback_from`; all failed/skipped → `ProvidersUnavailable`. Tests in `tests/unit/llm/test_router_fallback.py` (each row of the table)
- [ ] T026 [US2] Add daily usage tracking to `llm/router.py`: `INCR llm:usage:{provider}:{yyyymmdd}` (UTC, TTL 48 h), skip when usage ≥ 90% of `daily_requests`. Tests in `tests/unit/llm/test_router_quota.py`
- [ ] T027 [US2] Implement `llm/cache.py`: key from `llm/cache_key.py` (T009); Redis `llm:cache:{key}` TTL 24 h then Postgres `llm_cache` (`hits` incremented, `last_hit_at` updated); only schema-valid successes are cached; router checks the cache per provider before calling it and returns `cache_hit=true`. Tests in `tests/unit/llm/test_cache.py` and `tests/integration/agent/test_cache_pg.py` (hit survives Redis flush)
- [ ] T028 [P] [US2] Add would-have-cost (`price_per_mtok` × tokens) and cache/fallback attributes to the span in `llm/telemetry.py`; nightly cleanup of `llm_cache` rows not hit for 30 days as an arq cron in `workers/jobs.py`. Tests in `tests/unit/llm/test_telemetry.py`, `tests/unit/workers/test_jobs.py`
- [ ] T029 [US2] Map `ProvidersUnavailable` to `providers_unavailable` in `agent/runs.py` and the stream (`error` event with localized "temporarily unavailable, try later"); web `AnswerView.tsx` shows it. Tests in `tests/integration/api/test_research.py` and `web/tests/components/AnswerView.test.tsx`
- [ ] T030 [US2] Fallback demo `llm/demo_fallback.py` (`--fail-first` injects a 429 for the first provider) and an automated test `tests/integration/agent/test_fallback_demo.py` replaying cassettes. (owner action) Run it once live and save the trace link and output in `evals/reports/fallback-demo-<date>.md` (FR-028, SC-004)

**Checkpoint**: US1 + US2 = cited answers that survive provider failures.

---

## Phase 5: User Story 3 — Sign in, quotas and abuse protection (Priority: P2)

**Goal**: Google/GitHub sign-in, allowlist-gated beta, atomic daily quota, rate limits, Turnstile.

**Independent Test**: sign in via mocked OAuth for an allowlisted and a non-allowlisted email; use up a quota of 2 with concurrent requests; exceed rate limits; send a bad Turnstile token.

- [ ] T031 [P] [US3] Implement `api/guards/turnstile.py`: verify token against Cloudflare siteverify with `httpx` (remote IP passed), failure → `bot_check_failed`. Tests in `tests/unit/api/test_turnstile.py` with `MockTransport`
- [ ] T032 [US3] Implement `api/auth/oauth.py` and `api/routes/auth.py` per [contracts/http-api.md](./contracts/http-api.md): `POST /v1/auth/{provider}/start` (Turnstile required; `state` + PKCE verifier in Redis 10 min; `return_to` must be on `WEB_ORIGIN`), `GET /v1/auth/{provider}/callback` (code exchange, fetch email + name; GitHub uses the primary verified email; upsert `users` with lower-cased email; non-allowlisted → 302 `/beta-closed`; sets `session` (15 min) and `refresh` (7 days) cookies HttpOnly, Secure, SameSite=Lax, Domain=`SESSION_COOKIE_DOMAIN`), `POST /v1/auth/refresh` (rotates, revokes old `refresh:{id}`), `POST /v1/auth/logout`. Tests in `tests/integration/api/test_auth.py` with mocked provider endpoints (invalid state, unverified email, open-redirect attempt)
- [ ] T033 [P] [US3] Implement `api/auth/allowlist.py`: check with Redis cache `allow:{email_hash}` 60 s; CLI `python -m api.auth.allowlist add|remove|list` (emails lower-cased; list prints masked emails). `current_user` raises `beta_closed` for non-listed users. Tests in `tests/unit/api/test_allowlist.py` (removal takes effect within the cache TTL)
- [ ] T034 [US3] Implement `api/guards/quota.py`: Lua check-and-increment on `quota:{user_id}:{yyyymmdd}` (Europe/Istanbul day, TTL 48 h), limit `QUOTA_DAILY_QUESTIONS`; refund if the run fails before the first model call; `QuotaStatus` with `resets_at`; applied to `POST /v1/research`. Integration test in `tests/integration/api/test_quota.py`: 10 concurrent requests with limit 2 → exactly 2 accepted
- [ ] T035 [P] [US3] Implement `api/guards/ratelimit.py`: fixed-window counters `rl:user:{id}:{minute}` and `rl:ip:{ip_hash}:{minute}` (TTL 120 s), limits from settings, `rate_limited` with `Retry-After`; client IP from Cloudflare's `CF-Connecting-IP` header, trusted only when the request arrives from Caddy on the proxy network (otherwise the socket peer address); test that a spoofed header from outside the proxy is ignored. Tests in `tests/unit/api/test_ratelimit.py`
- [ ] T036 [US3] Implement `api/routes/me.py`: `GET /v1/me` (`display_name`, `email_masked`, `language`, `quota`), `PUT /v1/me` (`language` `tr`/`en`). Tests in `tests/integration/api/test_me.py`
- [ ] T037 [US3] Web auth: `web/src/app/signin/page.tsx` (Google and GitHub buttons, Turnstile widget via script tag, links to privacy and terms), `web/src/app/beta-closed/page.tsx`, `web/src/components/QuotaBadge.tsx` (remaining count; exhausted message with reset time), redirect to sign-in on `unauthorized`, silent `refresh` retry once. Vitest tests in `web/tests/components/` and `web/tests/lib/api.test.ts`

**Checkpoint**: allowlisted users can sign in; access is gated and quota-safe.

---

## Phase 6: User Story 4 — Legal pages and disclosures (Priority: P2)

**Goal**: privacy (KVKK), terms and disclaimer pages in TR and EN, reachable signed out.

**Independent Test**: open each page signed out in both languages; required statements present; sign-in page links to them.

- [ ] T038 [P] [US4] Draft `web/src/content/legal/{privacy,terms,disclaimer}.{tr,en}.md` for owner review: personal data stored = email and display name only; purpose; retention; KVKK rights and contact; third-party model providers receive only public disclosure text and some free tiers may use prompts for training; no investment advice. Run `anthropic-skills:ai-writing-patterns` on the drafts
- [ ] T039 [US4] Pages `web/src/app/privacy/page.tsx`, `terms/page.tsx`, `disclaimer/page.tsx` rendering the T038 content in the selected language; footer links on all pages. Vitest tests check each page renders the required statements in TR and EN; extend the Playwright smoke test to visit them signed out

---

## Phase 7: User Story 5 — Turkish/English interface (Priority: P3)

**Goal**: Turkish default, persistent English toggle, phone-width layout.

**Independent Test**: toggle, reload, language persists; no untranslated strings; screens usable at 375 px.

- [ ] T040 [US5] Persist the language choice: existing `web/src/i18n/useLanguage.ts` stores it locally and, when signed in, syncs with `PUT /v1/me`; `Accept-Language` sent on API calls. Vitest tests in `web/tests/i18n/useLanguage.test.ts`
- [ ] T041 [P] [US5] Add an i18n completeness test `web/tests/i18n/keys.test.ts` (every key in `tr.ts` exists in `en.ts` and vice versa) and a Playwright viewport check at 375×812 for ask, answer and sign-in pages in `web/tests/e2e/mobile.spec.ts`

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T042 [P] Log/trace scrub test `tests/integration/api/test_no_secrets_in_logs.py`: run a full fake run and sign-in, capture logs and spans, assert no email, JWT, OAuth code or API key appears (SC-008)
- [ ] T043 [P] Update `infra/docker-compose.yml` and the deploy workflow with the new API env vars (from the server `.env`, never in the repo) and the Vercel env (`NEXT_PUBLIC_TURNSTILE_SITE_KEY`); document in `docs/runbook.md`: provider key setup, OAuth app redirect URLs, allowlist CLI, quota changes
- [ ] T044 [P] ADR `docs/adr/0003-single-agent-and-router.md`: single loop instead of LangGraph for Phase 3, OpenAI-compatible client, cache key, API-hosted runs + Redis streams, JWT cookie sessions
- [ ] T045 Run [quickstart.md](./quickstart.md) end to end on staging/production; record results (SC-005: p95 of `time_to_first_event_ms` and `total_ms` from `runs.usage`, SC-006 cache hit rate, eval metrics) in `evals/reports/phase3-<date>.md`
- [ ] T046 (owner action) Add the owner and at least 3 test accounts (Google and GitHub) to the allowlist; on production each asks one Turkish and one English question and gets a cited answer; record SC-001 results in `evals/reports/phase3-<date>.md`

---

## Dependencies & Execution Order

- **Setup (T001–T003)** → **Foundational (T004–T011)** → user stories.
- **US1 (T012–T024)**: needs Foundational only (uses a minted test session, not real sign-in).
- **US2 (T025–T030)**: needs T009; independent of US1 code except T029 (touches `agent/runs.py`, after T018).
- **US3 (T031–T037)**: needs T005, T006, T010; T034 hooks into T019's route.
- **US4 (T038–T039)**: independent; T037 links to its pages.
- **US5 (T040–T041)**: after T023 and T036.
- **Polish**: after the stories it covers; T046 after T045.

Story order for delivery: US1 → US2 → US3 → US4 → US5 (US1 and US2 are both P1; phase acceptance needs all of US1–US4).

## Parallel Opportunities

- Setup: T002, T003 together.
- Foundational: T006, T007, T010, T011 together after T004/T005.
- US1: T012, T013, T014, T015, T016, T020, T022 together; then T017 → T018 → T019 → T021; T023 → T024.
- US2: T028 alongside T025–T027.
- US3: T031, T033, T035 together; then T032, T034, T036, T037.
- US4 can run entirely alongside US2/US3.

## Implementation Strategy

1. **MVP**: Setup + Foundational + US1 → cited answers with a test session and recorded responses; validate with the eval harness (T021).
2. **Beta-ready**: add US2 (fallback is a phase acceptance criterion), US3 (real sign-in, quotas), US4 (legal pages).
3. **Finish**: US5 and Polish, then run the acceptance check with test accounts (T046).
4. Per the branch-per-phase rule, each phase above gets its own branch; one task per PR.
