# Disclosly (working name) — Project Specification

> Multi-agent research platform that answers questions about company disclosures (Turkey: KAP; US: SEC EDGAR), shows every number with its source, verifies its own answers, and runs in production for real users on a zero-dollar LLM budget.

---

## 0. Instructions for the AI coding agent reading this file

- This file is the single source of truth. Read it fully before writing code.
- Work **phase by phase** (Section 20). Do not start a phase until the previous phase's acceptance criteria pass.
- Before each phase: propose a short plan (files to create/change, tests to add). After each phase: run lint, type checks, tests and evals, and report results.
- Never add a dependency, paid service or new infrastructure component that is not listed here without asking first. Every choice must fit the constraints in Section 2.
- Prefer simple, explicit code over clever abstractions. Type everything (Python type hints, Pydantic models, TypeScript strict).
- Every feature ships with tests. Every LLM-dependent feature ships with an eval case.
- Never put secrets in code, prompts, logs or the sandbox. Use environment variables.
- When something in this spec is ambiguous or seems wrong, stop and ask rather than guessing.
- Write code, comments, commit messages and docs in English. The product UI supports Turkish (default) and English.

---

## 1. Product

### 1.1 What it does
A user asks a question such as:
- "THYAO'nun son 4 çeyrekteki net borç / FAVÖK oranı nasıl değişti? Kaynak göster."
- "Compare Apple's and Microsoft's gross margin over the last 4 quarters and explain the difference from their filings."

The system:
1. Classifies the question (simple lookup vs. deep research) and, for deep research, shows a plan and an estimated number of model calls; the user approves or edits it (human-in-the-loop).
2. Streams agent progress live (which documents searched, which tools called, which calculations run).
3. Returns a structured report: tables, a chart, every number linked to its source (document + page/section), a confidence note, and the disclaimer "Yatırım tavsiyesi değildir / Not investment advice".
4. Remembers the user's watchlist and preferences (long-term memory).
5. Lets the user give 👍/👎 and flag "wrong source" on each answer.

### 1.2 Users
- **Primary:** Turkish retail investors (Numiver users) researching BIST companies, funds and IPO prospectuses (halka arz izahnameleri).
- **Secondary (demo for international recruiters):** English Q&A over SEC 10-K / 10-Q filings.
- **Power users / developers:** connect via MCP from their own AI client (Claude Desktop, Cursor). In that path the LLM cost is the user's; our server only answers tool calls.

### 1.3 Non-goals
- No buy/sell recommendations, price targets or portfolio advice (regulated activity in Turkey). The system provides information and calculations from documents only.
- No real-time market data or trading.
- No model training or fine-tuning in v1.

---

## 2. Hard constraints (non-negotiable)

| Constraint | Value | Consequence |
|---|---|---|
| LLM budget | **$0** | Only free API tiers + BYOK. No paid model calls. |
| Server | **1 VPS: 2 vCPU, 4 GB RAM, 40 GB disk** | No local chat LLM on the server. Only a small embedding model and a small reranker. Strict memory limits per container. |
| Frontend hosting | Vercel or Cloudflare Pages free plan | Next.js does not run on the VPS. |
| Observability | Langfuse Cloud, Grafana Cloud, Sentry — free plans | No self-hosted Langfuse/ClickHouse. Only a light OTel exporter on the VPS. |
| Batch embedding | Developer laptop (Fedora, ASUS ROG) | Documents are embedded offline, vectors uploaded to Postgres. |
| CI | GitHub Actions (public repo) | CI evals use recorded responses (cassettes), not live API calls. |
| Legal | Turkey (KVKK), investment-advice rules | Privacy policy, terms, disclaimer on every report, no advice. |
| Language | UI Turkish + English; answers in the question's language | Prompts and evals must cover both languages. |

Because of the free-tier limits, **the real bottleneck is the daily LLM quota, not the server**. Every design decision should minimise model calls (caching, routing, cheap-first).

---

## 3. Architecture

```
[Next.js web (Vercel)]           [Claude Desktop / Cursor (user's own LLM)]
          |                                       |
          v                                       v
[FastAPI: SSE, auth, quota]              [MCP server (spec 2026-07-28)]
          |                                       |
          +------------------+--------------------+
                             v
        [LangGraph graph + Postgres checkpointer + HITL interrupt]
          Router -> Planner -> Retriever -> Analyst -> Verifier -> Writer
                       ^__________________________________|
                         (Verifier sends back on failure, max N rounds)
                             |
     +-----------------------+------------------------+
     v                       v                        v
[Postgres + pgvector]  [Python sandbox]        [Model router]
 docs, chunks, vectors  no network, limited     Gemini -> Groq -> GitHub Models
 checkpoints, memory    CPU/RAM/time            -> OpenRouter :free -> Cloudflare
 cache, users, quotas                           + BYOK, response cache
     ^
[arq worker: fetch, parse, chunk; query-time embed/rerank]
[Laptop: batch embedding of documents -> upload]

All model/tool calls -> OpenTelemetry -> Langfuse Cloud / Grafana Cloud; errors -> Sentry
```

---

## 4. Tech stack (fixed choices)

| Layer | Choice |
|---|---|
| Language | Python 3.12+, TypeScript (strict) |
| API | FastAPI (async), Pydantic v2, SSE streaming, uvicorn (2 workers) |
| Orchestration | LangGraph 1.x, Postgres checkpointer, `interrupt` for HITL |
| LLM access | Own `ModelRouter` (Section 8). OpenAI-compatible HTTP clients where possible. No LangChain model wrappers required. |
| DB | PostgreSQL 16 + pgvector (HNSW index) + `tsvector` full-text (Turkish + English configs) |
| Cache / queue | Redis (`maxmemory 128mb`, `allkeys-lru`), arq for background jobs |
| Embeddings | Small multilingual model, 384 dims (e.g. `multilingual-e5-small`), via sentence-transformers on CPU |
| Reranker | Small cross-encoder (MiniLM class) on CPU; must handle Turkish acceptably — verify with evals, fall back to no-rerank if it hurts |
| Sandbox | Docker container: no network, read-only FS, `--memory=512m`, `--cpus=1`, 20 s timeout, max 1–2 concurrent |
| MCP | Official Python MCP SDK, spec revision 2026-07-28 (stateless), HTTP transport, OAuth |
| Frontend | Next.js + TypeScript + Tailwind, deployed on Vercel/Cloudflare Pages |
| Auth | Google + GitHub OAuth, JWT sessions |
| Observability | OpenTelemetry (GenAI semantic conventions) → Langfuse Cloud; Prometheus metrics → Grafana Cloud; Sentry |
| Product analytics | PostHog or Plausible free plan |
| Bot protection | Cloudflare Turnstile |
| Infra | Docker Compose, Caddy (TLS), Terraform (VPS + Cloudflare DNS), 2–4 GB swap file |
| Quality | ruff, mypy (strict), pytest + pytest-asyncio, ESLint, Playwright (1–2 smoke tests) |
| Migrations | Alembic |

---

## 5. Data sources and ingestion

### 5.1 Sources
1. **KAP (Turkey)** — financial statements, material event disclosures, fund reports, IPO prospectuses. Reuse the existing Numiver KAP/TEFAS pipeline and PDF OCR where possible. Respect KAP's terms of use and rate limits.
2. **SEC EDGAR (US)** — 10-K and 10-Q for ~20–30 large companies, last 2 years, plus XBRL `companyfacts` for structured numbers. Send a proper `User-Agent` and respect SEC fair-access rules.

Start v1 with **~20 BIST companies + ~20 US companies**. Grow based on user demand.

### 5.2 Pipeline (idempotent)
1. **Fetch** → store raw file temporarily, record `source_url`, `company`, `period`, `doc_type`, `published_at`, content hash. Skip if hash already exists.
2. **Parse** → HTML/PDF to structured text. Keep section structure (e.g. 10-K Item 7 MD&A, Item 8; KAP report sections). Extract tables separately as structured rows.
3. **Chunk** → section-aware, ~500–800 tokens with small overlap; never split a table row. Each chunk gets metadata: company, ticker, period, doc_type, section, page, language.
4. **Embed** → batch on the laptop, upload vectors; server embeds only queries and newly ingested small documents.
5. **Structured facts** → financial line items (revenue, net income, total debt, EBITDA, etc.) into a `financial_facts` table with source reference. Numbers in answers should come from here or from sandbox calculations, not from model memory.
6. **Cleanup** → delete raw PDFs/HTML after successful parse; keep text, tables, vectors. Keep disk usage under 25 GB.

---

## 6. Agent design (LangGraph)

### 6.1 State (typed)
```python
class ResearchState(TypedDict):
    run_id: str
    user_id: str
    question: str
    language: Literal["tr", "en"]
    mode: Literal["simple", "deep"]
    plan: list[PlanStep]
    plan_approved: bool
    evidence: Annotated[list[Evidence], add]      # chunks/facts with source refs
    calculations: Annotated[list[Calculation], add]
    draft: Report | None
    verification: VerificationResult | None
    verify_rounds: int
    budget: Budget                                # max_steps, max_model_calls, deadline
    usage: Usage                                  # steps, model_calls, tokens, providers used
    errors: Annotated[list[str], add]
```

### 6.2 Nodes
| Node | Responsibility | Model class |
|---|---|---|
| **Router** | Decide `simple` vs `deep`; detect language; reject advice-seeking questions politely | cheap |
| **Planner** | Break deep questions into steps; estimate model calls; trigger HITL interrupt for approval | strong |
| **Retriever** | Agentic RAG: write queries, hybrid search, rerank, judge sufficiency, rewrite query if insufficient (max 2 rewrites) | cheap |
| **Analyst** | Pull `financial_facts`, run sandbox code for ratios/growth/charts; numbers come from code | cheap/strong |
| **Verifier** | Check each claim has a supporting source; recompute numbers; on failure return to Planner with reasons (max 2 rounds) | strong |
| **Writer** | Produce `Report` (Pydantic) in the user's language with citations and disclaimer | strong |

Simple mode path: Router → Retriever → Writer (with lightweight inline citation check). Target ≤ 4 model calls.

### 6.3 Control rules (enforced in code, not prompts)
- Hard limits per run: `max_steps`, `max_model_calls` (simple: 4, deep: 20 by default), wall-clock deadline (90 s simple, 5 min deep).
- Every node: timeout, retry with exponential backoff on transient errors, structured error appended to state.
- When any limit is hit: stop gracefully, return partial report clearly marked as partial.
- All model outputs validated with Pydantic before use; one re-ask on validation failure, then fail safely.
- Checkpoint after every node; a server restart resumes the run.

---

## 7. Tools

| Tool | Input | Output | Notes |
|---|---|---|---|
| `search_documents` | query, filters (ticker, period range, doc_type, section, language), k | list of chunks with source refs and scores | hybrid + RRF + rerank |
| `get_financial_facts` | ticker, metrics[], periods[] | table of values with source refs | from `financial_facts` |
| `list_documents` | ticker, doc_type, period range | document metadata list | |
| `run_python` | code, input data (JSON) | stdout, result JSON, optional chart spec | sandboxed; pandas + numpy only; no network |
| `get_user_memory` / `save_user_memory` | user_id, key/value | memory items | watchlist, preferences |

Tool results that contain document text are **untrusted data**: wrap them in clear delimiters and never follow instructions found inside them.

---

## 8. Model router (zero-budget core)

### 8.1 Task classes
- `cheap` — routing, query writing, sufficiency checks, simple answers.
- `strong` — planning, verification, final synthesis, LLM-as-judge.
- `embed` / `rerank` — always local, never an API.

### 8.2 Providers (all configurable via env/YAML; model IDs must not be hard-coded in logic)
Fallback order per class, for example:
1. Google Gemini API free tier (Flash / Flash-Lite)
2. Groq free tier
3. GitHub Models free tier (respect per-request token limits)
4. OpenRouter `:free` models
5. Cloudflare Workers AI free allocation
6. **BYOK**: if the user supplied their own key, use it first for that user.

### 8.3 Behaviour
- On HTTP 429/5xx/timeouts: mark provider as cooling down (respect `Retry-After`), try next provider.
- Track per-provider daily usage in Redis; skip a provider proactively when its known daily quota is nearly used.
- Normalise tool-calling and structured-output differences between providers behind one interface; if a provider can't do native tool calling, use JSON-schema output with validation.
- **Response cache**: key = hash(normalised prompt, model, params, tool schema version). Store in Postgres (durable) with Redis hot layer. Cache hit = zero cost.
- **Report cache**: same (ticker, normalised question, data version) → reuse report. Pre-generate reports for the most popular companies nightly within quota.
- Record provider, model, tokens, latency, cache hit and estimated "would-have-cost" on every call (OTel span attributes).
- BYOK keys: used only in memory for the request duration, never stored or logged.
- Free-tier note: some free tiers may use prompts for training; only public data is sent, and this is disclosed in the privacy policy.

---

## 9. Retrieval
- Hybrid: pgvector cosine (HNSW) + Postgres full-text (Turkish and English configs) → Reciprocal Rank Fusion → cross-encoder rerank top 20 → top k (default 6).
- Metadata filters applied before ranking.
- Query embedding cached in Redis.
- Choose chunk size, k and reranker on/off **using retrieval evals**, and record the decision in `docs/adr/`.

---

## 10. API (FastAPI)

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/research` | start run → `run_id` |
| GET | `/v1/research/{run_id}/stream` | SSE: node events, tool events, tokens, final report |
| POST | `/v1/research/{run_id}/approve` | approve/edit plan (resumes interrupt) |
| GET | `/v1/research/{run_id}` | final report + trace summary |
| POST | `/v1/research/{run_id}/feedback` | 👍/👎, wrong-source flag, comment |
| GET | `/v1/companies`, `/v1/documents` | browse data |
| GET/PUT | `/v1/me`, `/v1/me/memory`, `/v1/me/byok` | profile, memory, BYOK key (session only) |
| GET | `/v1/examples` | precomputed example runs (no login needed) |
| GET | `/healthz`, `/readyz`, `/metrics` | ops |

Rate limits: per IP and per user. Daily run quota per user (e.g. 5 deep + 20 simple for free users; configurable). Turnstile on anonymous endpoints.

---

## 11. Frontend (Next.js)
- Landing page with 3–5 precomputed example reports (viewable without login).
- Ask page: question box, language auto-detect, example questions, plan approval dialog, live agent timeline (SSE), report view with tables, chart, clickable citations opening the source excerpt.
- History, watchlist, settings (language, BYOK key entry with clear explanation).
- Feedback buttons on every report.
- Quota indicator and friendly messages when quota is exhausted ("tomorrow" or "add your own key").
- Pages: privacy policy (KVKK), terms, disclaimer, status link, changelog.
- Mobile-friendly. Turkish default, English toggle.

---

## 12. MCP server
- Expose: `search_documents`, `get_financial_facts`, `list_documents` (read-only).
- Spec revision 2026-07-28 (stateless core). HTTP transport, OAuth, per-user rate limits.
- No LLM calls on our side in the MCP path.
- Document in README how to connect from Claude Desktop and Cursor; record a short demo video.

---

## 13. Security and guardrails
- Prompt injection: retrieved text and tool outputs are data; system prompt states this; delimiters around untrusted content; tool allowlist per node; output schema validation. Maintain an injection test set (≥ 30 cases, TR + EN) in evals.
- Advice refusal: Router detects "should I buy/sell", "price target" etc. and answers with information only plus disclaimer. Eval set ≥ 20 cases.
- Sandbox: no network, no secrets, resource limits, killed after timeout, fresh container per run (or pre-warmed pool of 1).
- Auth: OAuth only, no passwords stored. JWT with short expiry.
- PII: only email and display name stored. Logs and traces mask emails; BYOK keys never logged.
- Dependencies pinned; Dependabot enabled.

---

## 14. Evaluation

### 14.1 Datasets (versioned in `evals/datasets/`)
- `retrieval_golden` — ≥ 50 questions (TR + EN) with expected source chunks.
- `answers_golden` — ≥ 50 questions with expected numbers (tolerance) and expected sources. Include a FinanceBench subset for the SEC side.
- `injection` — ≥ 30 adversarial cases.
- `advice_refusal` — ≥ 20 cases.
- `from_feedback` — answers users marked 👎 / wrong source, after manual review.

### 14.2 Metrics
- Retrieval: recall@5, recall@10, MRR.
- Answers: numeric exact match within tolerance; faithfulness (every claim supported); citation precision; LLM-as-judge score for text quality.
- Judge calibration: ≥ 30 human-labelled examples; report agreement rate.
- Agent: steps, model calls, tool error rate, loops/terminations, partial-report rate.
- Cost/latency: model calls per run, cache hit rate, p50/p95 latency, estimated would-have-cost.

### 14.3 Execution
- **Every PR (CI):** deterministic tests + eval subset replayed from recorded responses (cassettes). Fails the build if any metric drops beyond configured tolerance vs. `main` baseline.
- **Nightly (on the VPS or laptop, within free quota):** full eval with live models; results written to `evals/reports/` and summarised in README.
- **On model/provider change:** full eval required before merge.

---

## 15. Observability
- OTel span per node, per model call, per tool call: provider, model, tokens, latency, cache hit, error, run_id, user_id (hashed).
- Dashboards (Grafana Cloud): requests, p50/p95 latency, error rate, model calls per run, cache hit rate, provider fallback rate, daily quota usage per provider, queue depth.
- Alerts: error rate spike, quota > 80 %, queue backlog, disk > 80 %, memory > 90 %.
- Any failed run can be replayed step by step from Langfuse trace + checkpoint.

---

## 16. Deployment (single 4 GB VPS)

### 16.1 Docker Compose services and memory limits
| Service | `mem_limit` |
|---|---|
| caddy | 64m |
| postgres (pgvector) | 1g (`shared_buffers=256MB`, `work_mem=8MB`, `max_connections=40`) |
| redis | 160m |
| api (FastAPI, 2 workers) | 512m |
| worker (arq + embed/rerank models) | 768m |
| mcp | 256m |
| otel-collector (light) | 128m |
| sandbox containers (on demand, max 1–2) | 512m each |

Plus a 2–4 GB swap file. Measure with `docker stats` and record real numbers in README.

### 16.2 Other
- Caddy for TLS and reverse proxy; Cloudflare DNS and proxy in front.
- Terraform: VPS, firewall, Cloudflare DNS records.
- Nightly Postgres backup (pg_dump, compressed) to free object storage or the laptop; test restore once.
- Zero-downtime-ish deploy: pull new image, run migrations, restart api/worker one by one.

---

## 17. CI/CD (GitHub Actions)
1. `ruff`, `mypy --strict`, ESLint, TypeScript check.
2. `pytest` (unit + integration with Postgres/Redis service containers).
3. Eval subset with cassettes + regression gate.
4. Build and push Docker images (multi-stage, `python:3.12-slim`).
5. Deploy to VPS on `main` (SSH, compose pull + up). Frontend auto-deploys via Vercel/Cloudflare.
6. `main` is protected; PRs required.

---

## 18. Repository structure
```
disclosly/
  api/            FastAPI app, routes, auth, quotas
  agent/          LangGraph graph, nodes, state, prompts
  llm/            ModelRouter, providers, cache, BYOK
  retrieval/      hybrid search, rerank, embeddings
  tools/          tool implementations
  sandbox/        sandbox runner + Dockerfile
  ingestion/      KAP + SEC fetchers, parsers, chunker, facts extractor
  workers/        arq jobs
  mcp_server/     MCP server
  web/            Next.js app
  evals/          datasets, harness, judge prompts, cassettes, reports
  infra/          docker-compose.yml, Caddyfile, terraform/, grafana/
  docs/           architecture.md, adr/, runbook.md, postmortems/
  .github/        workflows
  PROJECT_SPEC.md (this file)
  README.md
```

---

## 19. README (must contain, in English)
1. One sentence + live link + 2–3 minute demo video.
2. Architecture diagram + short flow description.
3. Eval results table with date, and how to rerun.
4. Cost & latency: model calls per run, cache hit rate, p95, "$0 LLM budget" design explanation (router, fallback, caching, BYOK, MCP offload).
5. Failure modes and mitigations.
6. Key decisions (LangGraph, pgvector, own router, sandbox) with links to ADRs.
7. Security model.
8. Real usage numbers (users, runs, 👍 rate, production bugs found and fixed).
9. Local setup in one command.

---

## 20. Phases and acceptance criteria

| Phase | Weeks | Deliverable | Acceptance criteria |
|---|---|---|---|
| 1. Walking skeleton | 1 | Monorepo, CI, compose, Caddy, empty API + web deployed | Push to `main` deploys automatically; `/healthz` live over HTTPS; web on Vercel |
| 2. Data + retrieval | 2–3 | KAP + SEC ingestion for ~40 companies; hybrid search; retrieval golden set | Idempotent pipeline; recall@10 measured and reported; chunk/rerank decision in ADR |
| 3. Single-agent MVP | 4 | ModelRouter with fallback + cache; one tool-calling agent; SSE UI with citations; auth, quotas, Turnstile, disclaimer, privacy page | Real users can sign in and get cited answers; provider fallback demonstrated; **closed beta to Numiver users** |
| 4. Multi-agent | 5–6 | LangGraph graph (Section 6), HITL plan approval, sandbox, verifier loop, memory, checkpoint resume | Server restart mid-run resumes; single vs multi-agent compared on evals and documented honestly |
| 5. Production hardening | 7–8 | Full evals + CI gate, OTel dashboards and alerts, guardrail sets, MCP server, feedback loop, nightly reports cache, load test, runbook, one postmortem | All Section 21 items checked; public launch; blog post; demo video |

---

## 21. Definition of done (production checklist)
- [ ] Hard limits (steps, model calls, deadline) enforced in code
- [ ] All model outputs schema-validated; safe failure path
- [ ] Provider fallback + cooldown + quota tracking working and visible in traces
- [ ] Response and report caching with measured hit rate
- [ ] BYOK works and keys are never stored/logged
- [ ] Every number in a report has a source; verifier recomputes numbers
- [ ] Advice refusal + disclaimer on every report
- [ ] Injection and refusal eval sets passing
- [ ] CI eval gate blocks regressions; nightly full eval runs
- [ ] Dashboards + alerts live; failed runs replayable
- [ ] Memory limits respected under load test; swap configured; backups tested
- [ ] Privacy policy (KVKK), terms, status page, changelog
- [ ] Onboarding: first result within ~30 seconds using examples
- [ ] Analytics: signups, first run, 7-day return, run success rate, 👍 rate
- [ ] README complete with real numbers

---

## 22. Open decisions (ask the owner before assuming)
- Final product name and domain (candidate: disclosly.com — check availability before buying; free fallback: subdomain of Numiver's domain).
- Integration depth with Numiver: separate app linked from Numiver vs. embedded "Derin Araştırma" feature calling this API.
- Exact free-user daily quotas.
- Which BIST companies and funds are in the first data set.
