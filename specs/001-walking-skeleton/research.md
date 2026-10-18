# Research: Phase 1 — Walking Skeleton

**Feature**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md) | **Date**: 2026-10-17

The Technical Context had no open NEEDS CLARIFICATION items after the spec clarifications
(domain `disclosly.com`, Terraform on Hetzner). This file records each design decision.

## R1. Python project layout and tooling

- **Decision**: One Python project at the repository root (`pyproject.toml`) containing the `api`
  and `evals` packages. Use `uv` for environments and a committed `uv.lock`. Run `ruff` (lint +
  format), `mypy --strict`, and `pytest` with `pytest-asyncio`, `pytest-cov`, and `httpx2`.
- **Rationale**: `PROJECT_SPEC.md` §18 puts Python packages at the top level. With one project,
  later packages (`agent/`, `llm/`, …) share one lockfile, one type-check run and one coverage
  number. `uv` gives a fast, hash-pinned lockfile (constitution "dependencies pinned"). It is
  already installed on the developer laptop.
- **Alternatives considered**: One `pyproject.toml` per package (a uv workspace). It adds
  configuration with no benefit for a single deployable. `pip-tools`/`requirements.txt` work, but
  they are slower and need a second tool for virtual environments.

## R2. Backend shape

- **Decision**: FastAPI app factory in `api/main.py`, run by uvicorn with 2 workers. Endpoints:
  `/healthz` (liveness, no dependency calls), `/readyz` (Postgres `SELECT 1` and Redis `PING`,
  each with a 2 s timeout), and `/metrics` (Prometheus text format). Configuration comes from
  `pydantic-settings`, and startup fails on missing required settings. Every error uses one error
  envelope with codes such as `not_found`, `validation_error`, `internal_error` and
  `dependency_unavailable`.
- **Rationale**: Liveness must not depend on the database, or a database outage would restart the
  API in a loop. Readiness checks the real dependencies (FR-006). Metrics are required by FR-007,
  and the Grafana Cloud scrape in Phase 5 will read them.
- **Alternatives considered**: One combined health endpoint is simpler, but it mixes liveness
  with readiness. Writing Prometheus text output by hand removes the dependency, but
  `prometheus-client` is small and handles multiprocess mode for 2 uvicorn workers.

## R3. Multiprocess metrics with 2 uvicorn workers

- **Decision**: Run `prometheus-client` in multiprocess mode. Set `PROMETHEUS_MULTIPROC_DIR` to a
  tmpfs path in the container and clear it at container start.
- **Rationale**: Each worker has its own registry. Without multiprocess mode, `/metrics` would
  show only the counts of the worker that served the scrape.
- **Alternatives considered**: One uvicorn worker. This conflicts with `PROJECT_SPEC.md` §4
  ("uvicorn (2 workers)").

## R4. Structured logging

- **Decision**: A stdlib `logging.Formatter` subclass writes one JSON object per line. Middleware
  adds a request ID (it reuses an incoming `X-Request-ID` header or generates a new one, and
  returns it in the response). Each request logs `route`, `method`, `status`, `latency_ms` and
  `request_id`. A redaction filter masks email-like strings and any field named like
  `*key*/*token*/*secret*/*password*`.
- **Rationale**: This meets FR-009 and constitution V without a new dependency.
- **Alternatives considered**: `structlog` is nicer to use, but it adds a dependency for a few
  dozen lines of code.

## R5. Database and migrations in Phase 1

- **Decision**: Deploy `pgvector/pgvector:pg16` and `redis:7-alpine` with the §16.1 memory
  limits and Postgres settings (`shared_buffers=256MB`, `work_mem=8MB`, `max_connections=40`).
  Alembic is set up with an empty baseline revision. The deploy runs `alembic upgrade head` as a
  one-off container before the API restarts. The Postgres driver is psycopg 3.
- **Rationale**: Readiness then checks real dependencies, and Phase 2 only adds tables. psycopg 3
  is the driver LangGraph's Postgres checkpointer uses (Phase 4), so the project needs only one
  driver.
- **Alternatives considered**: `asyncpg` is faster, but LangGraph would still need psycopg, which
  means two drivers. Leaving out the data stores until Phase 2 makes `/readyz` meaningless now
  and moves infrastructure risk to a later phase.

## R6. Container images and registry

- **Decision**: Multi-stage `api/Dockerfile` on `python:3.12-slim`, running as a non-root user,
  with a `HEALTHCHECK` on `/healthz`. Images go to GHCR as
  `ghcr.io/<owner>/disclosly-api:<git-sha>` (plus a `main` tag). The commit SHA is baked into the
  image as `APP_VERSION`.
- **Rationale**: GHCR is free for public repositories and authenticates with the workflow's
  `GITHUB_TOKEN`, so no new service or secret is needed. A SHA tag makes rollback exact.
- **Alternatives considered**: Docker Hub has pull-rate limits and needs another account.
  Building on the server costs RAM and CPU on a 4 GB box during deploys.

## R7. Deploy and rollback mechanism

- **Decision**: `deploy.yml` runs on `workflow_run` after `ci.yml` succeeds on `main`, with
  `concurrency: deploy-production` (no cancel-in-progress, so deploys run in order).
  The steps are:
  1. Build and push the image.
  2. SSH to the server as user `deploy` with a dedicated key from Actions secrets.
  3. Run `infra/scripts/deploy.sh <sha>`. The script records the current tag as the previous tag,
     writes the new tag to the server's `.env` (`API_IMAGE_TAG`), runs `docker compose pull api`,
     runs the migration container, then runs `docker compose up -d --no-deps api`.
  4. Poll `https://api.disclosly.com/healthz` until it reports the new version, for up to 60 s.
  5. If polling fails, restore the previous tag, run `up -d` again, and exit non-zero.

  Compose files and the Caddyfile are copied to the server on each deploy, so the server holds
  no hand-edited configuration.
- **Rationale**: This meets FR-021 to FR-024, SC-001 and SC-006 with plain SSH and shell. A few
  seconds of downtime during restart is accepted (spec Assumptions).
- **Alternatives considered**: Watchtower or another pull-based updater needs no SSH secret, but
  it has no CI-visible failure and no ordered rollback. Kamal or Docker Swarm add a new tool or
  orchestrator for one server. A blue/green pair of API containers is not needed at this scale.

## R8. TLS, Cloudflare and Caddy

- **Decision**: Cloudflare is the DNS host for `disclosly.com`. `api.disclosly.com` is a proxied
  A record to the server. The Cloudflare SSL mode is **Full (strict)**, and "Always Use HTTPS" is
  off, so Caddy does the redirect (FR-013). Caddy gets and renews a Let's Encrypt certificate with
  the HTTP-01 challenge through the proxy. The Caddyfile reverse-proxies to `api:8000`, sets
  security headers, and passes `X-Request-ID`.
- **Rationale**: `PROJECT_SPEC.md` §16.2 puts Cloudflare in front. Full (strict) keeps the
  Cloudflare-to-origin connection encrypted and verified. Stock Caddy works with no custom
  plugin build.
- **Alternatives considered**: A Cloudflare Origin CA certificate only works while traffic is
  proxied, and the private key must be handled by hand. The DNS-01 challenge needs a custom Caddy
  image with the Cloudflare plugin plus an API token on the server. Use DNS-01 only if HTTP-01
  through the proxy proves unreliable.

## R9. Terraform scope and state

- **Decision**: Terraform in `infra/terraform/` manages:
  - the Hetzner server: shared-vCPU class with 2 vCPU / 4 GB / 40 GB (the exact type name, e.g.
    `cx22` or its current successor, is a variable checked at apply time), Ubuntu 24.04, location
    `fsn1` (a variable);
  - the Hetzner SSH key;
  - the Hetzner Cloud Firewall: inbound 22, 80 and 443 only;
  - the Cloudflare DNS records `api` (proxied A), apex and `www` (Vercel targets);
  - the Cloudflare zone SSL settings.

  `cloud-init.yaml` sets up the server:
  - installs Docker Engine and the Compose plugin;
  - creates a `deploy` user in the docker group;
  - creates a 4 GB swap file with `vm.swappiness=10`;
  - turns off SSH password and root login;
  - enables `unattended-upgrades`;
  - enables Docker at boot.

  Compose services use `restart: unless-stopped` (FR-015). Terraform runs **from the maintainer's
  laptop**. State is local and gitignored, and the owner backs it up. Provider tokens come from
  environment variables (`HCLOUD_TOKEN`, `CLOUDFLARE_API_TOKEN`).
- **Rationale**: This meets FR-017 and FR-018. Infrastructure changes are rare and made by one
  person, so CI-driven Terraform and remote state are not worth their cost or secret surface now.
- **Alternatives considered**: Terraform Cloud free state is another account and service, so it
  needs approval. An S3-compatible remote state on Hetzner Object Storage costs money. Running
  `terraform apply` from CI adds high-privilege tokens to GitHub.

## R10. Web app, i18n and Vercel deploy

- **Decision**: Next.js 16 App Router, TypeScript strict, Tailwind 4, npm with a committed
  `package-lock.json`. One page renders the product name, a description, the disclaimer and a
  `BackendStatus` client component. That component fetches `NEXT_PUBLIC_API_BASE_URL/healthz`
  with a 5 s timeout and shows "available (version)" or a friendly unavailable state. i18n uses
  a typed dictionary (`tr.ts`, `en.ts`) with Turkish as the default. The language choice is kept
  in `localStorage`, read in a try/catch, and falls back to Turkish.

  Deployment: `vercel.json` sets `git.deploymentEnabled: false`, and `deploy.yml` runs the pinned
  Vercel CLI (`vercel deploy --prebuilt --prod`) with `VERCEL_TOKEN`, `VERCEL_ORG_ID` and
  `VERCEL_PROJECT_ID` from Actions secrets. The domains `disclosly.com` (primary) and
  `www.disclosly.com` (redirect) are attached in the Vercel project.
- **Rationale**: Vercel's Git integration would publish even when CI fails. Deploying from CI ties
  web releases to the same gate (FR-027, FR-019). The dictionary approach covers 2 languages and
  about 10 strings with no dependency.
- **Alternatives considered**: `next-intl` is better for many pages, so reconsider it in Phase 3.
  Vercel Git auto-deploy is simpler but skips the gate. Cloudflare Pages is excluded by the
  acceptance criterion.

## R11. CI quality gates and security

- **Decision**: `ci.yml` runs on `pull_request` and `push: main` with separate jobs, each a
  required check:

  | Check | Contents |
  |---|---|
  | `lint` | `ruff check`, `ruff format --check`, ESLint, Prettier `--check`, `terraform fmt -check` |
  | `typecheck` | `mypy --strict`, `tsc --noEmit` |
  | `test-backend` | pytest with Postgres and Redis service containers; `--cov-fail-under=80` |
  | `test-web` | Vitest with an 80% line threshold, `next build`, Playwright smoke against `next start` |
  | `evals` | `python -m evals.run --cassettes-only`, which passes on the empty suite |
  | `build` | API Docker build, `docker compose config` for both compose files, `terraform validate` |
  | `security` | `pip-audit` (via `uv export`), `npm audit --audit-level=high --omit=dev`, gitleaks (pinned CLI over full history) |

  `codeql.yml` covers Python and JavaScript/TypeScript. GitHub secret scanning and push
  protection are turned on in the repository settings. `dependabot.yml` covers `uv`, `npm`,
  `docker`, `github-actions` and `terraform`. All Actions are pinned by commit SHA.
- **Rationale**: These map one-to-one to the constitution's Quality Gates table (FR-019, FR-020,
  FR-026). Separate jobs make each gate a separate required status check. Both dependency
  audits cover runtime dependencies only (`uv export --no-dev`, `npm audit --omit=dev`): dev
  tools never ship in the API image or the Vercel bundle. Dev-only advisories stay visible
  through Dependabot alerts. Decided 2026-10-18 after an unpatched high advisory in `braces`
  (GHSA-vfj7-8cjw-p6xm, reached only through `eslint-config-next`) blocked the gate.
- **Alternatives considered**: One monolithic CI job is simpler, but a single check name cannot
  show which gate failed. The `gitleaks-action` wrapper is avoided because the gitleaks CLI binary
  is enough and has no license question.

## R12. Dependencies requiring owner approval

`CLAUDE.md` and the constitution forbid adding anything not listed in `PROJECT_SPEC.md` §4/§16 (or
named in the constitution) without explicit approval. Items already listed: FastAPI, Pydantic v2,
uvicorn, Alembic, ruff, mypy, pytest, pytest-asyncio, Next.js, TypeScript, Tailwind, ESLint,
Prettier, Playwright, Docker Compose, Caddy, PostgreSQL + pgvector, Redis, Terraform, pip-audit,
npm audit, CodeQL.

**Needs approval:**

| Item | Kind | Why needed | Fallback if rejected |
|---|---|---|---|
| `uv` | Python tooling | Lockfile with hashes, venv management | pip-tools + venv |
| `pydantic-settings` | runtime | Typed, fail-fast environment config (FR-010) | Hand-written loader on `os.environ` + Pydantic model |
| `psycopg[binary]` 3 | runtime | Postgres readiness check; same driver LangGraph needs later | asyncpg (second driver later) |
| `redis` (redis-py) | runtime | Redis readiness check; arq depends on it later | none practical |
| `SQLAlchemy` | runtime (transitive) | Required by Alembic | none (Alembic needs it) |
| `prometheus-client` | runtime | `/metrics`, multiprocess (FR-007) | Hand-written text exposition, 1 worker |
| `pytest-cov` | dev | Coverage gate | `coverage` directly |
| `httpx2` | dev | FastAPI/Starlette `TestClient` requirement. Pydantic-maintained continuation of `httpx`; Starlette 1.8 deprecates plain `httpx` for the test client. Replaced `httpx` on 2026-10-17 (owner request). | `httpx` (deprecated by Starlette for `TestClient`) |
| Vitest, `@vitest/coverage-v8`, `@testing-library/react`, `jsdom` | web dev | Web unit tests + 80% coverage gate | Jest + equivalents |
| Vercel CLI (pinned, CI only) | CI tool | Gated web deploy (R10) | Vercel Git auto-deploy (skips CI gate) |
| gitleaks CLI (pinned, CI only) | CI tool | Secret scanning gate | GitHub secret scanning only (not a PR check) |
| Terraform providers `hetznercloud/hcloud`, `cloudflare/cloudflare` | infra | FR-017 | none (required by the decision) |
| GHCR | service (free) | Image registry | Docker Hub (rate limits, extra account) |

**Approval**: The owner approved all rows as listed on 2026-10-17. Any dependency not in this
table or in `PROJECT_SPEC.md` §4/§16 still needs separate approval.

## R13. Server sizing check

- **Decision**: The Phase 1 memory budget is caddy 64m + postgres 1g + redis 160m + api 512m =
  about 1.7 GB limit. The expected idle use is under 700 MB. `docker stats` numbers are recorded
  in the README after the first deploy (SC-008).
- **Rationale**: This leaves about 2 GB for the worker, MCP and sandbox in later phases, as
  §16.1 plans.
