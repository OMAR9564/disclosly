# ADR 0003: Single agent loop and model router

- Status: accepted
- Date: 2026-10-21
- Source: `specs/003-single-agent-mvp/research.md`, contracts `model-router.md`,
  `agent-answer.md`, `sse-events.md`, `http-api.md`

## Context

Phase 3 ships the first answering product: one question in, a cited answer out, on free model
tiers only ($0 budget). Free tiers fail often (429s, outages, daily caps) and differ in quality.
The answer must cite every number, and the user must see progress while the agent works.

## Decisions

### 1. One plain loop, not LangGraph

`agent/loop.py` is a single async loop: cheap-model calls with tools, then one forced
strong-model call that must return the `Answer` schema. Limits (4 model calls, 6 tool calls,
90 seconds) are checked in code, not left to the model. One re-ask is allowed when validation
fails and a call is left; otherwise the run ends `partial` with the validated points only.

A graph framework adds a dependency and a state model we do not need for one agent. Multi-agent
planning in a later phase can revisit this; the loop's inputs (router, tools, budget, emit) are
the seams a graph would replace.

### 2. One OpenAI-compatible client for every provider

Gemini, Groq, GitHub Models, OpenRouter and Cloudflare Workers AI all expose an OpenAI-compatible
chat endpoint. `llm/client.py` is one `httpx` call with tool-call normalisation and a JSON-schema
mode for providers without native tools. Provider SDKs would mean five dependencies and five
error models.

### 3. Ordered fallback with cooldowns

`llm/router.py` walks the providers listed for a task class (`cheap`, `strong`) in order. A
failure puts the provider in a Redis cooldown sized by failure type (429: `Retry-After` or 60 s;
5xx and timeouts: 60 s; bad output: 30 s; 400/401/403: 1 hour). Daily usage counters skip a
provider at 90% of its free quota. When every provider is cooling down, the run fails with
`providers_unavailable` instead of waiting.

### 4. Cache key over the canonical request

The cache key is sha256 of canonical JSON: normalised messages (NFC, collapsed whitespace),
model, temperature, max tokens, tool schema version and response schema. Hits are served from
Redis (24 h) and then Postgres `llm_cache` (pruned after 30 days without a hit). Only
schema-valid responses are cached, so a bad answer is never replayed. Bumping
`tool_schema_version` invalidates old entries when the tools change.

### 5. Runs hosted in the API, events in Redis streams

`POST /v1/research` starts the loop as an asyncio task in the API process and returns a run id.
Events go to `run:{id}:events` (a Redis stream, 24 h after the run ends), and
`GET /v1/research/{id}/stream` serves them over SSE with `Last-Event-ID` resume. The final answer
and citations are stored in Postgres, so a reload after the stream expires still shows the
answer. A queue worker was not used for runs: a run is mostly waiting on HTTP, and the API
already holds the user's connection. On startup, runs left `running` are marked `failed`
(`interrupted`).

Document search is the exception. The embedding and rerank models do not fit the API's 512 MB
limit, so searches go to a separate `search` worker over the arq queue `arq:search` (T047).

### 6. Sessions as JWT cookies

Sign-in is OAuth (Google, GitHub) with PKCE and a one-time state in Redis. The API sets a 15-minute
HS256 JWT `session` cookie and a 7-day opaque `refresh` cookie (rotated on use, revocable in
Redis). Both are HttpOnly, Secure, SameSite=Lax on `.disclosly.com`, so the web app on
`disclosly.com` and the API on `api.disclosly.com` share them without tokens in JavaScript. The
JWT carries a hash of the email so the allowlist can be checked on every request without a
database read.

## Consequences

- A deploy restarts the API and interrupts running runs. They are marked `failed` on startup,
  and their quota is not refunded.
- Fallback quality varies by provider; the eval gate (citation coverage 100%) runs against
  recorded cassettes, not live providers.
- The worker image is still built by hand; until CI builds it, the runbook covers the steps.
