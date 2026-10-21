# Quickstart: Single-Agent MVP validation

## Prerequisites

- Phase 2 corpus loaded (local compose or VPS).
- `.env` with at least two provider keys (e.g. `GEMINI_API_KEY`, `GROQ_API_KEY`), Google and
  GitHub OAuth client ids/secrets, `JWT_SECRET`, `TURNSTILE_SECRET_KEY` (Cloudflare's test keys
  work locally). See `.env.example`.

## 1. Offline checks (CI equivalent)

```bash
uv run pytest tests/unit/llm tests/unit/agent tests/unit/tools tests/unit/api
uv run pytest -m integration tests/integration/api tests/integration/agent
uv run python -m evals.run agent --replay
```

Expected: all pass; agent eval report shows citation coverage 100%, refusal 100%,
injection pass 100%, numeric match ≥ 80% (SC-002, SC-003).

## 2. Fallback demo (FR-028, SC-004)

```bash
uv run python -m llm.demo_fallback --fail-first
```

Expected: first provider marked cooling down, answer served by the second, trace shows
`fallback_from`. Run once live and save the trace link in `evals/reports/`.

## 3. Allowlist and sign-in

```bash
uv run python -m api.auth.allowlist add tester@example.com
```

Sign in at the web app with that account → ask page. Sign in with a non-listed account →
closed-beta message (US3).

## 4. End-to-end question

Ask "THYAO'nun 2025 net kârı nedir?" → progress events within 3 s, Turkish answer with
citations, disclaimer at the end; clicking a citation shows the excerpt containing the number.
Repeat the same question → `model_called` events show `cache_hit: true`.

## 5. Quota

Set `QUOTA_DAILY_QUESTIONS=2`, ask three questions → third returns `quota_exhausted` with reset
time; quota badge shows 0.

## 6. Legal pages

Open `/privacy`, `/terms`, `/disclaimer` signed out in TR and EN.
