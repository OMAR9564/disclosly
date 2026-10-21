# Research: Single-Agent MVP

## R1. Provider access

- **Decision**: Talk to every provider through the OpenAI-compatible chat completions API with
  `httpx` (Gemini's OpenAI endpoint, Groq, GitHub Models, OpenRouter, Cloudflare Workers AI).
  Base URL, model id, key env var, daily request quota and per-token reference price per
  provider live in `llm/providers.yaml`.
- **Rationale**: One request/response shape, native tool calling on all five, no SDK
  dependencies (PROJECT_SPEC §4: "No LangChain model wrappers required").
- **Alternatives**: vendor SDKs (five dependencies, five shapes); LiteLLM (large dependency,
  more than we need).

## R2. Fallback and quota tracking

- **Decision**: Provider order per task class from config. Retry triggers: HTTP 429, 5xx,
  connect/read timeout (15 s per call), empty content with no tool call, or invalid tool-call
  JSON. On trigger: set Redis key `llm:cooldown:{provider}` with TTL = `Retry-After` or 60 s
  default, move to the next provider. Daily usage: `INCR llm:usage:{provider}:{yyyymmdd}`
  (UTC, TTL 48 h); skip when ≥ 90% of the configured quota. If all are skipped or fail, raise
  `ProvidersUnavailable`.
- **Rationale**: Matches §8.3; Redis keys with TTL need no cleanup job.
- **Alternatives**: in-process state (lost across the two uvicorn workers).

## R3. Response cache

- **Decision**: Key = SHA-256 of canonical JSON of (normalised messages, model id, temperature,
  max tokens, tool schema version). Normalisation: trim and collapse whitespace, NFC. Lookup
  Redis (`llm:cache:{key}`, TTL 24 h) then Postgres `llm_cache`; write both. Only successful,
  schema-valid responses are cached. Temperature is 0 for all agent calls.
- **Rationale**: §8.4; temperature 0 makes cache hits meaningful. Postgres keeps hits across
  Redis LRU eviction.
- **Alternatives**: Redis only (lost on eviction under 128 MB).

## R4. Sessions

- **Decision**: OAuth authorization-code flow (with PKCE and `state`) handled by the API for
  Google and GitHub using `httpx`. On callback the API upserts the user, checks the allowlist,
  and sets an HttpOnly, Secure, SameSite=Lax cookie on `.disclosly.com` holding an HS256 JWT
  (15 min) plus a refresh cookie (7 days, rotated, id stored in Redis so it can be revoked).
  JWTs signed with **PyJWT** (new dependency, approved by owner 2026-10-21).
- **Rationale**: PROJECT_SPEC §4/§13 require OAuth + short JWT. Web (apex) and API
  (`api.disclosly.com`) are same-site, so the cookie works with `EventSource`, which cannot
  send custom headers. PyJWT is small, pure Python, widely audited; hand-rolled JWT is a
  security risk.
- **Alternatives**: Auth.js in Next.js (session then lives on Vercel and the API must trust it;
  adds a frontend dependency); opaque Redis sessions (no dependency, revocable, but departs
  from the JWT choice in §4).

## R5. Beta allowlist

- **Decision**: Postgres table `beta_allowlist(email)` managed with a CLI
  (`python -m api.auth.allowlist add|remove|list`). Checked on every authenticated request via
  a 60 s Redis cache, so changes apply within a minute without redeploy.
- **Rationale**: FR-021 answer (owner-managed email allowlist).

## R6. Quotas and rate limits

- **Decision**: Daily quota: Lua script that checks and increments `quota:{user}:{yyyymmdd}`
  atomically (Europe/Istanbul day, resets at 00:00 TRT). Refunded if the run fails before the
  first model call. Rate limits: fixed window counters, 30 req/min per user, 60 req/min per IP,
  configurable. Client IP comes from `CF-Connecting-IP` (Cloudflare is in front, so
  `X-Forwarded-For` from Caddy would only show Cloudflare addresses), trusted only from Caddy.
- **Rationale**: Atomic check-and-increment satisfies the concurrent-tabs edge case (SC-007).
- **Alternatives**: Postgres row lock (slower, more connections).

## R7. Run execution and streaming

- **Decision**: `POST /v1/research` creates a `runs` row and starts an asyncio task in the
  same worker. Events are `XADD`ed to Redis stream `run:{id}:events` (TTL 24 h after finish).
  `GET /v1/research/{id}/stream` reads the stream from the start (or `Last-Event-ID`) and sends
  SSE with `StreamingResponse`; a heartbeat comment every 15 s. Final answer is also persisted
  in `runs`. On API restart, runs left `running` are marked `failed` at startup (resume is a
  Phase 4 goal).
- **Rationale**: Reload-safe streaming without a new queue; ~1,000 runs/day fits in the API.
- **Alternatives**: arq worker (extra hop, worker memory is already used by the models);
  sse-starlette (dependency not needed).

## R8. Agent loop and citations

- **Decision**: Loop: system prompt + question → `cheap` call with tools → execute tool calls
  → repeat; the last allowed call is forced to produce the final `Answer` JSON with the
  `strong` class. Budget: 4 model calls, 90 s deadline, 6 tool calls. Every tool result item
  carries a `cite_id` (`c:<chunk_id>` or `f:<fact_id>`). The answer validator rejects any
  `cite_id` not returned in this run, and any number in the answer text not present in a cited
  chunk or fact value (normalised for Turkish/English separators). Failure → one re-ask; then a
  safe answer "could not produce a verified answer" with what was found.
- **Rationale**: Enforces FR-008/FR-010 in code; prepares the Phase 4 verifier.

## R9. Advice refusal and injection

- **Decision**: Advice detection: keyword/regex list (TR + EN) first; ambiguous cases fall to
  the agent's system prompt rule. Tool output is wrapped in
  `<untrusted_document cite_id=...>…</untrusted_document>` and the prompt says contents are
  data. Seed eval sets for this phase: 20 advice, 15 injection, 20 answer cases (grown to the
  §14 sizes in Phase 5).

## R10. Bot protection and frontend

- **Decision**: Cloudflare Turnstile on the sign-in start endpoint (the only anonymous action);
  server verifies the token with `httpx` against siteverify. Web: native `EventSource` with
  `withCredentials`, Tailwind, existing i18n module, no new packages.

## R11. Telemetry

- **Decision**: OTel spans `gen_ai.chat` with attributes `gen_ai.system`,
  `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, plus
  `disclosly.cache_hit`, `disclosly.would_have_cost_usd`, `disclosly.fallback_from`. Export to
  Langfuse Cloud via the existing light OTel setup when its env vars are set; otherwise no-op.
