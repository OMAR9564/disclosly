# Implementation Plan: Phase 1 — Walking Skeleton

**Branch**: `001-walking-skeleton` | **Date**: 2026-10-17 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-walking-skeleton/spec.md`

## Summary

Build the thinnest production path for Disclosly: a monorepo with an "empty" FastAPI backend
(`/healthz`, `/readyz`, `/metrics`, structured errors and JSON logs), a placeholder Next.js page
(TR default + EN, disclaimer, live backend status), and the delivery pipeline around them.
GitHub Actions runs every constitution Quality Gate on each PR. On `main` it builds the API image,
pushes it to GHCR, deploys it over SSH to one Hetzner server running Docker Compose behind Caddy
(HTTPS at `api.disclosly.com`), verifies `/healthz` and rolls back on failure. The web app is
deployed to Vercel (`disclosly.com`) from the same workflow, only after CI passes. Terraform
provisions the Hetzner server, its firewall and the Cloudflare DNS records. Details and
alternatives: [research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.12 (backend, evals); TypeScript 5.x strict on Node.js 24 LTS (web); HCL for Terraform ≥ 1.9

**Primary Dependencies**: Backend — FastAPI, Pydantic v2, pydantic-settings, uvicorn, Alembic (+ SQLAlchemy), psycopg 3, redis-py, prometheus-client. Web — Next.js 16 (App Router), React 19, Tailwind CSS 4. Infra — Docker Compose, Caddy 2, PostgreSQL 16 + pgvector image, Redis 7, Terraform with `hetznercloud/hcloud` and `cloudflare/cloudflare` providers. Items not in `PROJECT_SPEC.md` §4 are listed in [research.md §R12](./research.md#r12-dependencies-requiring-owner-approval), approved by the owner on 2026-10-17.

**Storage**: PostgreSQL 16 + pgvector and Redis are deployed and checked by `/readyz`; no application tables (empty Alembic baseline only)

**Testing**: pytest + pytest-asyncio + pytest-cov + httpx (backend unit and integration against CI service containers); Vitest + Testing Library + coverage-v8 (web unit); Playwright (1 smoke test against the built web app); `terraform validate` + `terraform fmt -check`; `docker compose config` validation

**Target Platform**: Backend — Linux containers on a Hetzner Cloud VPS (2 vCPU / 4 GB / 40 GB, Ubuntu 24.04). Web — Vercel free plan. CI — GitHub-hosted Ubuntu runners.

**Project Type**: Web service + web application (monorepo, layout from `PROJECT_SPEC.md` §18)

**Performance Goals**: `/healthz` p95 < 50 ms at server; merge-to-live < 15 min (SC-001); recovery after reboot < 5 min (SC-007)

**Constraints**: Container memory limits from `PROJECT_SPEC.md` §16.1 (caddy 64m, postgres 1g, redis 160m, api 512m); idle total < 2 GB (SC-008); 2–4 GB swap; $0 recurring cost except the VPS and domain; no secrets in repo; public repo

**Scale/Scope**: 1 maintainer, 1 server, 4 containers, 3 backend endpoints, 1 web page, 2 languages

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle / Rule | How this plan complies | Status |
|---|---|---|
| I. Spec-First | Approved spec with clarifications exists at `specs/001-walking-skeleton/spec.md`; plan traces every decision to FR/SC IDs. | PASS |
| II. Test-First | Every endpoint, config loader, logger, error handler and UI component gets tests; coverage gate ≥ 80% backend and web, separately; Playwright smoke; placeholder eval suite runs from cassettes (no live calls). | PASS |
| III. Simplicity | One Python project, one Next.js app, one compose file + dev override, one CI workflow + one deploy workflow. No worker, MCP, sandbox, OTel collector yet. Stdlib JSON log formatter instead of a logging library; own 2-language dictionary instead of an i18n library. | PASS |
| III. New dependencies justified | Every dependency not in `PROJECT_SPEC.md` §4 is listed with justification in research.md §R12; owner approved all on 2026-10-17. | PASS |
| IV. Security & No Secrets | `.env.example` only; secrets in GitHub Actions secrets, server `.env` (mode 600) and local Terraform vars; gitleaks secret scan in CI; GitHub secret scanning + push protection; DB/Redis ports not exposed; SSH key-only. | PASS |
| V. Observability | JSON request logs with request ID; `/metrics` (Prometheus format); stable error envelope, no stack traces to clients; no emails or secrets in logs. OTel export to Langfuse/Grafana is deferred to Phase 5 (spec Assumptions), and spans are not yet required because there are no model or tool calls. | PASS |
| Quality Gates (CI) | Separate required checks: `lint`, `typecheck`, `test-backend`, `test-web`, `coverage` (inside each test job, fails < 80%), `evals`, `build`, `security` (pip-audit, npm audit high+, gitleaks, CodeQL). | PASS |
| Git & PR workflow | Branch ruleset on `main`: PR required, required checks, squash only, no force push (FR-025). One task = one PR. | PASS |
| AI Agent Rules | R12 dependencies approved; anything else still needs approval; no constitution edits. | PASS |
| Technical Constraints | Python 3.12 / FastAPI / Pydantic v2 / Alembic; Next.js TS strict on Vercel (not the VPS); Postgres 16 + pgvector; Redis; Compose with memory limit per container; disclaimer on the page. | PASS |

**Gate result (pre-research)**: PASS. New dependencies (R12) approved by the owner on 2026-10-17.

**Gate result (post-design)**: PASS. The design artifacts (data-model, contracts, quickstart) add no new components, services or dependencies beyond R12.

## Project Structure

### Documentation (this feature)

```text
specs/001-walking-skeleton/
├── plan.md              # This file
├── research.md          # Phase 0 decisions
├── data-model.md        # Phase 1 entities (no DB tables)
├── quickstart.md        # Phase 1 validation guide
├── contracts/
│   ├── openapi.yaml     # /healthz, /readyz, /metrics, error envelope
│   ├── configuration.md # Environment variable contract
│   └── web-page.md      # Placeholder page UI contract
├── checklists/
│   └── requirements.md
└── tasks.md             # /speckit-tasks output (not created here)
```

### Source Code (repository root)

```text
pyproject.toml            # single Python project (api + evals), ruff/mypy/pytest config
uv.lock
Makefile                  # up, down, logs, test, lint, typecheck (one-command entry points)
.env.example
README.md
LICENSE  CONTRIBUTING.md  CODE_OF_CONDUCT.md  SECURITY.md

api/                      # FastAPI app (package `api`)
├── __init__.py
├── main.py               # app factory, middleware, routers
├── config.py             # pydantic-settings Settings, fail-fast
├── errors.py             # error envelope + exception handlers
├── logging.py            # JSON formatter, request-ID middleware
├── metrics.py            # Prometheus registry + middleware
├── health.py             # /healthz, /readyz, dependency checks
├── migrations/           # Alembic env + empty baseline revision
└── Dockerfile            # multi-stage, python:3.12-slim, non-root

evals/
├── __init__.py
├── run.py                # cassette-only runner; passes on empty suite
├── datasets/.gitkeep
└── cassettes/.gitkeep

tests/
├── unit/                 # config, errors, logging, metrics, health (deps mocked)
└── integration/          # app against real Postgres + Redis (CI service containers)

web/                      # Next.js app
├── package.json  package-lock.json  tsconfig.json  next.config.ts
├── eslint.config.mjs  .prettierrc  vitest.config.ts  playwright.config.ts
├── vercel.json           # disables Git auto-deploy; deploy happens from CI
├── src/app/              # layout.tsx, page.tsx
├── src/components/       # LanguageToggle, BackendStatus, Disclaimer
├── src/i18n/             # tr.ts, en.ts, dictionary + hook
├── src/lib/health.ts     # typed /healthz client
├── tests/unit/           # Vitest + Testing Library
└── tests/e2e/smoke.spec.ts

infra/
├── docker-compose.yml        # production: caddy, api, postgres, redis (mem_limit each)
├── docker-compose.dev.yml    # local override: build api from source, expose ports, no TLS
├── Caddyfile
├── scripts/deploy.sh         # pull → migrate → up → health check → rollback
└── terraform/
    ├── main.tf  variables.tf  outputs.tf  versions.tf
    ├── cloud-init.yaml       # Docker, deploy user, swap, SSH hardening, unattended upgrades
    └── terraform.tfvars.example

docs/
├── architecture.md
├── runbook.md            # deploy, rollback, reboot, cert, server rebuild
└── adr/0001-walking-skeleton-delivery.md

.github/
├── workflows/ci.yml          # PR + main: all quality gates
├── workflows/deploy.yml      # main after CI: image → GHCR → VPS; web → Vercel
├── workflows/codeql.yml
├── dependabot.yml
├── CODEOWNERS
├── PULL_REQUEST_TEMPLATE.md
└── ISSUE_TEMPLATE/
```

**Structure Decision**: Follow `PROJECT_SPEC.md` §18 literally: top-level Python packages (`api/`,
`evals/`) in one Python project at the repository root, the web app in `web/`, infrastructure in
`infra/`. Folders for later phases (`agent/`, `llm/`, `retrieval/`, `tools/`, `sandbox/`,
`ingestion/`, `workers/`, `mcp_server/`) are not created until their phase (FR-001).

## Complexity Tracking

No constitution violations. Table intentionally empty.
