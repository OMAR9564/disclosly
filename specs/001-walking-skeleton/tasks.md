---

description: "Task list for Phase 1 — Walking Skeleton"
---

# Tasks: Phase 1 — Walking Skeleton

**Input**: Design documents from `specs/001-walking-skeleton/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: REQUIRED. Constitution II (Test-First) says every task ships with tests, with coverage ≥ 80%. Tests are part of each task, not separate tasks. Write them first in the same task, see them fail, then implement. This keeps "one task = one PR" (constitution, Git & PR Workflow) with a green CI on every PR.

**Organization**: Tasks are grouped by user story. Phases follow **dependency order**, not only spec priority. US2 (backend live over HTTPS) and US5 (quality gate) must exist before US1 (automatic deploy) can work. P2 stories (US4, US3) follow.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story from spec.md (US1–US5)
- **(owner action)**: Needs the owner's accounts, tokens or GitHub settings. An agent prepares the files and docs; the owner performs the action.

## Path Conventions

Paths follow plan.md "Source Code": Python packages `api/`, `evals/` and `tests/` at the repo root; web app in `web/`; infrastructure in `infra/`; docs in `docs/`; workflows in `.github/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Repository skeleton, toolchains and policy files.

- [X] T001 Create root scaffolding: `.gitignore` (Python, Node, `.env`, `infra/terraform/.terraform/`, `infra/terraform/*.tfstate*`, `infra/terraform/*.tfvars` except `*.tfvars.example`, Playwright/coverage output), `.editorconfig`, and empty folders with `.gitkeep`: `tests/unit/`, `tests/integration/`, `evals/datasets/`, `evals/cassettes/`, `docs/adr/`, `infra/scripts/`, `infra/terraform/`
- [X] T002 Create `pyproject.toml` (project `disclosly`, `requires-python = ">=3.12"`) and `uv.lock` with the approved runtime deps (fastapi, uvicorn[standard], pydantic v2, pydantic-settings, alembic, sqlalchemy, psycopg[binary] 3, redis, prometheus-client) and dev deps (ruff, mypy, pytest, pytest-asyncio, pytest-cov, httpx, pip-audit). Configure ruff (lint + format, line length 100), `[tool.mypy] strict = true`, pytest (`asyncio_mode = "auto"`, marker `integration`, `testpaths = ["tests"]`). Create `api/__init__.py`, `evals/__init__.py` and smoke test `tests/unit/test_package.py`
- [X] T003 [P] Initialize the Next.js 16 app in `web/` (App Router, TypeScript `strict: true`, Tailwind 4, ESLint, Prettier, npm with `package-lock.json`, Node 24 in `web/.nvmrc`). Add Vitest + `@testing-library/react` + `jsdom` + `@vitest/coverage-v8` with an 80% lines threshold in `web/vitest.config.ts`, and Playwright in `web/playwright.config.ts` (Chromium only, `webServer: npm run start`). Add npm scripts `lint`, `format:check`, `typecheck`, `test`, `test:e2e` and `build`. Remove the template boilerplate and keep one trivial passing test in `web/tests/unit/`
- [X] T004 [P] Create `.env.example` with every variable from `contracts/configuration.md` (API, Postgres, Compose/Caddy, Web), placeholder values only (`CHANGE_ME`, `ops@example.com`, `http://localhost:3000`)
- [X] T005 [P] Create `Makefile` with targets `up`, `down`, `logs`, `web`, `lint`, `fmt`, `typecheck` and `test`, calling `uv run …`, `npm --prefix web run …` and `docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml …`. Targets whose files do not exist yet must fail with a clear message
- [X] T006 [P] Create policy files: `LICENSE` (MIT, copyright holder "OMAR ALFAROUK ALMOHAMAD", year 2026), `CONTRIBUTING.md` (Spec Kit flow, one task = one PR, Conventional Commits, local gates), `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `SECURITY.md` (private vulnerability reporting via GitHub advisories), `.github/CODEOWNERS`, `.github/PULL_REQUEST_TEMPLATE.md` (spec + task ID reference and a checklist with one line per constitution principle and Quality Gate), and `.github/ISSUE_TEMPLATE/{bug_report.yml,feature_request.yml,config.yml}`
- [X] T007 [P] Create `.github/dependabot.yml` with weekly updates for `uv` (`/`), `npm` (`/web`), `docker` (`/api`), `github-actions` (`/`) and `terraform` (`/infra/terraform`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Baseline CI, the backend core (config, errors, logging, app factory), the container image and local Compose. Every user story depends on these.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T008 Create `.github/workflows/ci.yml` (on `pull_request` and `push` to `main`; `permissions: contents: read`; `concurrency` per ref; every action pinned by commit SHA) with separate jobs: `lint` (`ruff check`, `ruff format --check`, `npm run lint`, `npm run format:check`), `typecheck` (`mypy`, `npm run typecheck`), `test-backend` (`uv run pytest -m "not integration"`) and `test-web` (`npm test`)
- [ ] T009 Implement `Settings` in `api/config.py` with pydantic-settings, writing `tests/unit/test_config.py` first. Use the fields from `contracts/configuration.md`. `APP_ENV` is "one of `development`, `test`, `production`". `DATABASE_URL` is a `SecretStr`. `CORS_ALLOWED_ORIGINS` is "a comma-separated list of absolute `https://` origins (`http://localhost:*` is allowed only when `APP_ENV=development`)". A missing required variable makes the process "exit non-zero at startup; the log names the variable, never its value". Secret fields "never appear in `repr`, logs or error messages". Add a cached `get_settings()`
- [ ] T010 [P] Implement the error envelope and exception handlers in `api/errors.py`, writing `tests/unit/test_errors.py` first. The body shape is `{"error": {code, message, request_id, details}}` and the codes come from the data-model catalogue: `not_found` 404, `method_not_allowed` 405, `validation_error` 422 (only code with non-null `details` = list of `{loc, msg}`), `dependency_unavailable` 503, `internal_error` 500. An unhandled exception returns `internal_error` with a generic message, and the test asserts that no stack trace or exception text is in the body
- [ ] T011 [P] Implement the JSON log formatter, redaction filter and request-ID middleware in `api/logging.py`, writing `tests/unit/test_logging.py` first. Each request writes one line with `ts` (ISO-8601 UTC), `level`, `msg="request"`, `request_id`, `method`, `route` ("the route template, not the raw path"), `status`, `latency_ms` and `version`. Level is `info` for 2xx–4xx and `error` for 5xx. The request ID is "from `X-Request-ID` if it is valid (≤ 64 chars, `[A-Za-z0-9-]`), else a new UUID4", and is echoed in the response header. Email-like values and fields matching `*key*`, `*token*`, `*secret*`, `*password*` or `authorization` become `"[REDACTED]"`
- [ ] T012 Implement `create_app()` in `api/main.py`, which wires settings (T009), error handlers (T010), the logging middleware (T011) and CORS restricted to `CORS_ALLOWED_ORIGINS`. Write `tests/unit/test_app.py` first: an allowed origin gets the CORS header, a disallowed origin does not, an unknown path returns 404 `not_found` with an `X-Request-ID` header, and a wrong method returns 405 `method_not_allowed`
- [ ] T013 Create `api/Dockerfile` and `.dockerignore`. The Dockerfile is multi-stage on `python:3.12-slim`, installs dependencies with uv from `uv.lock`, runs as a non-root user, takes build arg `APP_VERSION` into an env var, sets `PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus`, and has `api/entrypoint.sh` clear that directory before `uvicorn api.main:create_app --factory --workers 2 --host 0.0.0.0 --port 8000`. Add `HEALTHCHECK` on `/healthz`
- [ ] T014 Create `infra/docker-compose.yml` (production) with these services, each `restart: unless-stopped` with the `mem_limit` from §16.1:
  - `caddy` (`caddy:2`, 64m, the only service publishing 80/443, with volumes for `/data` and `/config`);
  - `api` (`ghcr.io/<owner>/disclosly-api:${API_IMAGE_TAG}`, 512m, tmpfs `/tmp/prometheus`);
  - `postgres` (`pgvector/pgvector:pg16`, 1g, `-c shared_buffers=256MB -c work_mem=8MB -c max_connections=40`, named volume, `pg_isready` healthcheck);
  - `redis` (`redis:7-alpine`, 160m, `--maxmemory 128mb --maxmemory-policy allkeys-lru`);
  - `migrate` (same image as `api`, profile `migrate`, runs `alembic upgrade head`).

  Also create `infra/docker-compose.dev.yml`: it builds `api` from `api/Dockerfile` with `APP_VERSION=dev`, publishes `8000`, publishes `5432` and `6379` on `127.0.0.1` only, and turns off `caddy` via a profile
- [ ] T015 Set up Alembic: `alembic.ini` at the root, `api/migrations/env.py` reading `DATABASE_URL` from `Settings`, and empty baseline revision `api/migrations/versions/0001_baseline.py`. Write `tests/integration/test_migrations.py` (marker `integration`, runs `upgrade head` and `downgrade base`). Add Postgres (`pgvector/pgvector:pg16`) and Redis service containers to the `test-backend` CI job and make it run all tests, including the `integration` marker

**Checkpoint**: The backend image builds, the local Compose stack starts, and CI runs lint, typecheck and tests on every PR.

---

## Phase 3: User Story 2 — Anyone can confirm the backend is alive over HTTPS (Priority: P1)

**Goal**: `https://api.disclosly.com/healthz` answers over trusted HTTPS, HTTP redirects to HTTPS, `/readyz` reports dependency state, and `/metrics` is exposed.

**Independent Test**: quickstart V3, V4 and V6. Run `curl https://api.disclosly.com/healthz`: it returns 200 with a valid certificate from two networks. `http://` redirects. `/readyz` returns 503 when Redis is stopped.

- [ ] T016 [US2] Implement `GET /healthz` in `api/health.py` and register it in `api/main.py`. Write `tests/unit/test_health.py` first. The body is exactly `{"status": "ok", "version": <APP_VERSION>, "service": "disclosly-api"}` (`additionalProperties: false`, version is "Git SHA or 'dev'"), and the test asserts that the handler makes no Postgres or Redis calls
- [ ] T017 [US2] Implement `GET /readyz` in `api/health.py`:
  - Postgres check: `SELECT 1` via psycopg 3. Redis check: `PING` via redis-py. Each check runs concurrently with a 2 s timeout.
  - Each `DependencyCheck` has `status` (`ok` | `error`), `latency_ms ≥ 0` and `error` (null or one of `timeout`, `connection_refused`, `auth_failed`, `unknown`), and never contains a connection string or a stack trace.
  - Top-level `status` is `ready` (HTTP 200) only if both checks are `ok`, else `not_ready` (HTTP 503).

  Write `tests/unit/test_readyz.py` first with fake clients covering each error mapping. Add `tests/integration/test_readyz.py` against the real CI services
- [ ] T018 [P] [US2] Implement Prometheus metrics in `api/metrics.py`: multiprocess registry, middleware recording `http_requests_total{method,route,status}` and histogram `http_request_duration_seconds{method,route}` (route template, not raw path), and `GET /metrics` in text format 0.0.4. Write `tests/unit/test_metrics.py` first (counters increase after a request; unknown paths are recorded under one fixed route label to avoid cardinality blow-up)
- [ ] T019 [P] [US2] Create `infra/Caddyfile`:
  - site `{$API_DOMAIN}` with global `email {$ACME_EMAIL}`;
  - `reverse_proxy api:8000` with `X-Request-ID` passed through;
  - headers `Strict-Transport-Security "max-age=31536000"`, `X-Content-Type-Options nosniff`, `Referrer-Policy no-referrer` and `-Server`;
  - automatic HTTP to HTTPS redirect.

  Check it with `docker run --rm -v ./infra/Caddyfile:/etc/caddy/Caddyfile caddy:2 caddy validate --config /etc/caddy/Caddyfile` (with the env vars set)
- [ ] T020 [P] [US2] Create the Terraform Hetzner config. Pin `hetznercloud/hcloud` and `cloudflare/cloudflare` in `infra/terraform/versions.tf`.
  - `infra/terraform/variables.tf`: `server_type` (2 vCPU / 4 GB / 40 GB class; check the current type name before apply), `location` (default `fsn1`), `image` (default `ubuntu-24.04`), `ssh_public_keys` (list), `domain` (default `disclosly.com`), and Vercel DNS targets.
  - `infra/terraform/main.tf`: `hcloud_ssh_key`, `hcloud_server` with `user_data = file("cloud-init.yaml")`, and `hcloud_firewall` allowing inbound only TCP 22, 80 and 443 (attached to the server).
  - `infra/terraform/outputs.tf` (server IPv4) and `infra/terraform/terraform.tfvars.example`.

  The check is `terraform fmt -check` and `terraform validate`
- [ ] T021 [P] [US2] Create `infra/terraform/cloud-init.yaml`. It must:
  - install Docker Engine and the Compose plugin from Docker's apt repo;
  - create user `deploy` in the `docker` group with the deploy public key;
  - create `/swapfile` of 4 GB with `vm.swappiness=10`, persisted in `/etc/fstab`;
  - set `PasswordAuthentication no` and `PermitRootLogin no`;
  - install and enable `unattended-upgrades`;
  - `systemctl enable docker`;
  - create `/home/deploy/disclosly/.deploy/`
- [ ] T022 [US2] Add the Cloudflare resources to `infra/terraform/main.tf`:
  - a zone data source for `var.domain`;
  - DNS records `api` (A to the server IPv4, proxied), the apex and `www` (Vercel targets from variables, not proxied);
  - zone settings `ssl = "strict"`, `always_use_https = "off"` and `min_tls_version = "1.2"`.

  Use the resource names of the pinned provider major version. The check is `terraform validate`
- [ ] T023 [US2] Create `infra/scripts/deploy.sh <sha>` (`set -euo pipefail`):
  1. Read the current `API_IMAGE_TAG` from `~/disclosly/.env` and save it to `.deploy/previous`.
  2. Write the new tag and run `docker compose pull api`.
  3. Run `docker compose --profile migrate run --rm migrate`. If it fails, exit non-zero and leave the old version running.
  4. Run `docker compose up -d --no-deps api`.
  5. Poll `https://$API_DOMAIN/healthz` until `version == <sha>`, for up to 60 s.
  6. If polling fails, restore the previous tag, run `up -d --no-deps api` and exit non-zero (rolled back).

  Write `tests/integration/test_deploy_script.py` first. It runs the script with fake `docker` and `curl` executables on `PATH` and covers success, migrate failure and health-check failure with rollback. Lint the script with `shellcheck`, which is preinstalled on GitHub runners
- [ ] T024 [US2] (owner action) Provision and do the first manual deploy, and document it in `docs/runbook.md` ("Provisioning" and "First deploy" sections):
  1. Point the `disclosly.com` nameservers to Cloudflare.
  2. Create the zone-scoped Cloudflare token and the Hetzner token.
  3. Run `terraform apply`.
  4. Create the server `~/disclosly/.env` (mode 600) from `.env.example` with real values.
  5. Push the first image to GHCR manually.
  6. Copy the compose files and Caddyfile, then run `deploy.sh`.
  7. Validate quickstart V4 and V6.

**Checkpoint**: The backend is live over HTTPS on `api.disclosly.com` and can be deployed by hand with health-checked rollback.

---

## Phase 4: User Story 5 — Every pull request is checked by the full quality gate (Priority: P1)

**Goal**: CI enforces every constitution Quality Gate as a separate required check, and `main` is protected.

**Independent Test**: quickstart V5 steps 3–4. Throwaway PRs with a lint error, a type error, a failing test, coverage < 80% and a fake secret each turn their check red and cannot merge. A direct push to `main` is rejected.

- [ ] T025 [US5] Enforce coverage: in `pyproject.toml`, `--cov=api --cov=evals --cov-report=term-missing --cov-fail-under=80` for the `test-backend` job; confirm the `web/vitest.config.ts` lines threshold of 80 fails `npm test` when it is not met. Add `test-web` steps `npm run build` and `npm run test:e2e` (Playwright installs Chromium with `--with-deps`) in `.github/workflows/ci.yml`
- [ ] T026 [P] [US5] Implement the cassette-only eval runner in `evals/run.py` (`python -m evals.run --cassettes-only`). It discovers `evals/datasets/*.jsonl`, fails if any case has no recorded cassette in `evals/cassettes/`, and never makes network calls. On an empty suite it prints `0 cases, passed` and exits 0. Write `tests/unit/test_evals_runner.py` first (empty suite passes; a case without a cassette fails). Add CI job `evals` in `.github/workflows/ci.yml`
- [ ] T027 [P] [US5] Add CI job `build` in `.github/workflows/ci.yml` that runs:
  - `docker build -f api/Dockerfile --build-arg APP_VERSION=${{ github.sha }} .` (no push);
  - `docker compose --env-file .env.example -f infra/docker-compose.yml config` and the same with the dev override;
  - `terraform -chdir=infra/terraform fmt -check`, `init -backend=false` and `validate`;
  - `caddy validate` on `infra/Caddyfile` in the `caddy:2` image;
  - `shellcheck infra/scripts/*.sh api/entrypoint.sh`;
  - `terraform fmt -check` in the `lint` job.
- [ ] T028 [P] [US5] Add CI job `security` in `.github/workflows/ci.yml`:
  - `uv export --frozen --no-dev --format requirements-txt | uv run pip-audit -r /dev/stdin --strict`;
  - `npm --prefix web audit --audit-level=high`;
  - pinned gitleaks CLI `detect` over the full history (`fetch-depth: 0`), with `.gitleaks.toml` allowlisting only the placeholder values in `.env.example`.
- [ ] T029 [P] [US5] Create `.github/workflows/codeql.yml` for `python` and `javascript-typescript`, triggered on PR, push to `main` and a weekly schedule, with actions pinned by SHA
- [ ] T030 [US5] (owner action) Configure the repository settings and document them in `docs/runbook.md` ("Repository settings"):
  - a branch ruleset on `main`: PR required; required checks `lint`, `typecheck`, `test-backend`, `test-web`, `evals`, `build`, `security` and `CodeQL`; squash merge only; block force pushes and deletions; no bypass actors;
  - secret scanning with push protection, Dependabot alerts and private vulnerability reporting.

  Validate quickstart V5 steps 3–4

**Checkpoint**: No PR can reach `main` without passing every gate.

---

## Phase 5: User Story 1 — Merge to main ships to production automatically (Priority: P1) 🎯 MVP

**Goal**: A squash merge to `main` that passes CI deploys the API (with rollback) and the web app with no manual step.

**Independent Test**: quickstart V5 steps 1–2 and V7. After a trivial merge, `/healthz` and the web status show the new SHA within 15 minutes. A deliberately broken `/healthz` rolls back automatically.

**Depends on**: US2 (server, Caddy, `deploy.sh`) and US5 (CI workflow named `CI` with all gates).

- [ ] T031 [US1] Create job `api` in `.github/workflows/deploy.yml`:
  - Triggers: `on: workflow_run` with `workflows: [CI]`, `types: [completed]`, `branches: [main]`; the job condition requires `conclusion == 'success'` and `event == 'push'`.
  - Settings: `concurrency: {group: deploy-production, cancel-in-progress: false}`, `permissions: {contents: read, packages: write}`, actions pinned by SHA.
  - Steps:
    1. Check out `head_sha`.
    2. Build and push `ghcr.io/<owner>/disclosly-api:<head_sha>` and `:main` with `APP_VERSION=<head_sha>`.
    3. Write `DEPLOY_SSH_KEY` and `DEPLOY_KNOWN_HOSTS` from secrets (never `StrictHostKeyChecking=no`).
    4. `scp` `infra/docker-compose.yml`, `infra/Caddyfile` and `infra/scripts/deploy.sh` to `deploy@$DEPLOY_HOST:~/disclosly/`.
    5. `ssh … ./deploy.sh <head_sha>`.
    6. Run a final `curl` that checks the `/healthz` version.
- [ ] T032 [US1] Create job `web` in `.github/workflows/deploy.yml`. It uses the same trigger and condition but does not depend on `api`. Steps:
  1. `npx vercel@<pinned> pull --yes --environment=production`.
  2. `vercel build --prod` with `NEXT_PUBLIC_API_BASE_URL=https://api.disclosly.com` and `NEXT_PUBLIC_APP_VERSION=<head_sha>`.
  3. `vercel deploy --prebuilt --prod`.

  Secrets are `VERCEL_TOKEN`, `VERCEL_ORG_ID` and `VERCEL_PROJECT_ID`. Create `web/vercel.json` with `{"git": {"deploymentEnabled": false}}`
- [ ] T033 [US1] (owner action) Set up the external accounts and document them in `docs/runbook.md` ("Deploy secrets" and "Vercel"):
  - Create the Vercel project with root directory `web/`.
  - Attach the domains `disclosly.com` (primary) and `www.disclosly.com` (redirect to apex), and put the Vercel DNS targets into the Terraform variables.
  - Generate a deploy-only ed25519 key for user `deploy`.
  - Add the GitHub secrets `DEPLOY_SSH_KEY`, `DEPLOY_HOST`, `DEPLOY_KNOWN_HOSTS`, `VERCEL_TOKEN`, `VERCEL_ORG_ID` and `VERCEL_PROJECT_ID`.
- [ ] T034 [US1] Validate end to end and document the deploy and rollback procedure in `docs/runbook.md` ("Deploy" and "Rollback"). Run quickstart V5 steps 1–2 (merge-to-live < 15 min) and V7 (a broken `/healthz` rolls back, with < 1 min of failed checks), then revert the broken change through a PR

**Checkpoint**: Phase 1 acceptance criteria met: a push to `main` deploys automatically, `/healthz` is live over HTTPS, and the web app is on Vercel.

---

## Phase 6: User Story 4 — Contributor runs the whole stack locally with one command (Priority: P2)

**Goal**: `make up` starts the full local stack and `make down` stops it. The README explains setup.

**Independent Test**: quickstart V1 on a clean clone. Copy `.env.example` and run `make up`. The local `/healthz` returns 200 within 10 minutes (excluding first image pulls), and `make down` stops all services.

- [ ] T035 [US4] Finalize the `Makefile` targets:
  - `up`: runs `docker compose … up -d --build --wait`, then polls `http://localhost:8000/readyz` for up to 120 s with a clear failure message.
  - `down`: stops and removes the containers and keeps the volumes. `down-v` also removes the volumes.
  - `web`: runs `npm --prefix web run dev` with `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`.

  Add a CI step to the `build` job in `.github/workflows/ci.yml` that runs `cp .env.example .env && make up && curl -fsS localhost:8000/healthz && make down`
- [ ] T036 [P] [US4] Write `README.md` with the following content, and leave placeholders for the other §19 sections, which later phases fill in:
  - the one-sentence description;
  - live links to `https://disclosly.com` and `https://api.disclosly.com/healthz`;
  - prerequisites (Docker + Compose plugin, uv, Node 24, make);
  - the local setup (`cp .env.example .env`, `make up`, `make web`, `make down`);
  - the local quality gates (`make lint typecheck test`);
  - links to `docs/runbook.md` and `specs/`.

**Checkpoint**: A new contributor gets a working local stack from the README alone.

---

## Phase 7: User Story 3 — Visitors can open the web front end (Priority: P2)

**Goal**: `https://disclosly.com` shows the placeholder page in Turkish by default, with an English toggle, the disclaimer, and live backend status.

**Independent Test**: quickstart V9 on desktop and mobile. Check the TR default, the toggle to EN that persists, the visible disclaimer, `available` with the SHA, and friendly `unavailable` when the API is stopped.

- [ ] T037 [P] [US3] Create the typed i18n dictionary in `web/src/i18n/tr.ts`, `web/src/i18n/en.ts` and `web/src/i18n/index.ts`, plus `useLanguage()` in `web/src/i18n/useLanguage.ts`. Use the exact strings from `contracts/web-page.md`: the disclaimer is "Yatırım tavsiyesi değildir" / "Not investment advice", plus the status texts for `loading`, `available` and `unavailable` and the toggle labels "English" / "Türkçe". The default is `tr`. The choice is persisted in `localStorage`, and read and write are wrapped in try/catch with a fallback to `tr`. Write `web/tests/unit/i18n.test.ts` first: both dictionaries have identical keys, the default is TR, and the hook falls back when storage throws
- [ ] T038 [P] [US3] Implement `fetchHealth()` in `web/src/lib/health.ts`. It calls `${NEXT_PUBLIC_API_BASE_URL}/healthz` with a "5 s" `AbortController` timeout and checks that the body matches `{status: "ok", version: string, service: "disclosly-api"}`. It returns `{state: "available", version}` or `{state: "unavailable"}` on non-200, invalid body, network error or timeout. Write `web/tests/unit/health.test.ts` first with a mocked `fetch` covering each case
- [ ] T039 [US3] Implement components, writing `web/tests/unit/components.test.tsx` first:
  - `web/src/components/BackendStatus.tsx`: `data-testid="backend-status"` and `data-state="loading|available|unavailable"`, showing the version when `available`;
  - `web/src/components/LanguageToggle.tsx`: `data-testid="language-toggle"`, a native `<button>` so it is keyboard-operable;
  - `web/src/components/Disclaimer.tsx`: `data-testid="disclaimer"`, always rendered.
- [ ] T040 [US3] Implement `web/src/app/layout.tsx` and `web/src/app/page.tsx`:
  - `<html lang>` is synced with the active language (`tr`/`en`);
  - the page uses `data-testid="product-name"` and `data-testid="tagline"` and renders `Disclaimer`, `BackendStatus` and `LanguageToggle`;
  - the layout is responsive at 360 px with no horizontal scroll and meets WCAG AA contrast.

  Write `web/tests/unit/page.test.tsx` first: the TR render shows every test id, and toggling switches all visible text to EN
- [ ] T041 [US3] Write the Playwright smoke test `web/tests/e2e/smoke.spec.ts` against `next start`. It checks that:
  - the page loads in Turkish with the disclaimer visible;
  - the toggle switches to English and the choice persists after a reload;
  - `backend-status` reaches `data-state="unavailable"` when `/healthz` is routed to a 503, and `available` when it is routed to a valid body;
  - there is no horizontal scroll at a 360 px viewport.
- [ ] T042 [US3] Validate quickstart V9 in production: desktop and mobile, TR/EN, `www` redirects to the apex, and the API stopped then restarted shows `unavailable` and then `available`

**Checkpoint**: All five user stories work independently.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T043 [P] Write `docs/architecture.md`: a Phase 1 diagram (Vercel web → Cloudflare → Caddy → api → postgres/redis; GitHub Actions → GHCR → SSH deploy) and a request/deploy flow description
- [ ] T044 [P] Write `docs/adr/0001-walking-skeleton-delivery.md` recording research.md R7 (deploy and rollback), R8 (TLS via Cloudflare Full strict + Caddy HTTP-01), R9 (Terraform with local state) and R10 (Vercel deploy from CI only), with alternatives
- [ ] T045 [P] Complete `docs/runbook.md` with sections for: server reboot, certificate renewal check, full server rebuild from Terraform, secret rotation (deploy key, Vercel token, Postgres password), Terraform state backup, and reading logs (`docker compose logs api`)
- [ ] T046 Run quickstart V8 (reboot recovery < 5 min; `docker stats --no-stream` with each container under its limit and total < 2 GB) and record the measured memory numbers in `README.md` (SC-008)
- [ ] T047 Update `PROJECT_SPEC.md` §22 to mark the domain decision as resolved (`disclosly.com`, Hetzner + Terraform) with the date. This is a spec change, so the owner reviews it in its own PR
- [ ] T048 Run the full quickstart V1–V9, confirm SC-001 to SC-009, and record the results in `specs/001-walking-skeleton/checklists/acceptance.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup. T008 (CI) should merge first so every later PR is checked. Blocks all user stories.
- **US2 (Phase 3)**: Depends on Foundational.
- **US5 (Phase 4)**: Depends on Foundational. Independent of US2, so it can run in parallel.
- **US1 (Phase 5)**: Depends on **US2** (server, Caddy, `deploy.sh`) and **US5** (gated CI workflow).
- **US4 (Phase 6)**: Depends on Foundational only. It can start right after Phase 2.
- **US3 (Phase 7)**: T037–T041 depend on Setup (T003) only. T042 needs US1 (production deploy).
- **Polish (Phase 8)**: After all stories.

### User Story Dependencies

```text
Setup ─> Foundational ─┬─> US2 ─┐
                       ├─> US5 ─┴─> US1 ─> T042 (US3 prod check) ─> Polish
                       ├─> US4
                       └─> US3 (T037–T041, code + tests)
```

### Within Each Task

- Write the tests first and confirm they fail, then implement until green, all in the same PR.
- Backend: config → errors/logging → app factory → endpoints.
- Infra: Terraform → cloud-init → apply (owner) → deploy script → first deploy.

### Parallel Opportunities

- Setup: T003–T007 in parallel after T001–T002.
- Foundational: T010 and T011 in parallel after T009.
- After Foundational, US2, US5, US4 and US3 (T037–T041) can all proceed in parallel.
- Inside US2: T018, T019, T020 and T021 in parallel. Inside US5: T026–T029 in parallel. Inside US3: T037 and T038 in parallel.

---

## Parallel Example: User Story 2

```bash
Task: "T018 [P] [US2] Prometheus metrics in api/metrics.py with tests/unit/test_metrics.py"
Task: "T019 [P] [US2] Caddyfile in infra/Caddyfile"
Task: "T020 [P] [US2] Terraform Hetzner config in infra/terraform/{versions,variables,main,outputs}.tf"
Task: "T021 [P] [US2] Server bootstrap in infra/terraform/cloud-init.yaml"
```

## Parallel Example: User Story 5

```bash
Task: "T026 [P] [US5] Eval runner in evals/run.py + CI job evals"
Task: "T027 [P] [US5] CI job build"
Task: "T028 [P] [US5] CI job security"
Task: "T029 [P] [US5] CodeQL workflow"
```

## Parallel Example: User Story 3

```bash
Task: "T037 [P] [US3] i18n dictionary in web/src/i18n/"
Task: "T038 [P] [US3] fetchHealth in web/src/lib/health.ts"
```

---

## Implementation Strategy

### MVP (Phase 1 acceptance criteria)

1. Setup → Foundational (T008 first).
2. US2 and US5 in parallel.
3. US1.
4. **STOP and VALIDATE**: the §20 Phase 1 acceptance criteria are push-to-deploy, `/healthz` over HTTPS and web on Vercel. US1 can deploy the default `web/` page before US3 is finished.

### Incremental Delivery

1. Foundation → backend image + local Compose + baseline CI.
2. US2 → backend live over HTTPS (manual deploy).
3. US5 → full gate + protected `main`.
4. US1 → automatic deploys (MVP).
5. US4 → one-command local setup + README.
6. US3 → real placeholder page.
7. Polish → docs, measurements, full quickstart.

---

## Notes

- One task = one PR (constitution). The owner makes all commits and merges; agents only propose commit messages (`CLAUDE.md` Hard rules).
- `(owner action)` tasks need the owner's accounts and tokens. An agent may prepare files and docs but must not handle real secrets.
- **LICENSE (T006)**: MIT, chosen by the owner on 2026-10-17. The license covers code only; KAP/SEC data stays under its own terms and no raw data goes into the repo.
- Never weaken tests or gates to make a task pass. If a gate blocks a task, stop and ask.
- Any dependency not in research.md R12 or `PROJECT_SPEC.md` §4/§16 needs owner approval first.
