# Feature Specification: Phase 1 — Walking Skeleton

**Feature Branch**: `001-walking-skeleton`

**Created**: 2026-10-17

**Status**: Draft

**Input**: User description: "Phase 1 walking skeleton from PROJECT_SPEC.md §20"

## Clarifications

### Session 2026-10-17

- Q: Which public hostname serves the backend in Phase 1? → A: `disclosly.com` (purchased by the owner); backend at `api.disclosly.com`.
- Q: Is server and DNS provisioning via infrastructure-as-code part of Phase 1, and which VPS provider? → A: Yes, infrastructure-as-code in Phase 1; provider is Hetzner.

**Source**: `PROJECT_SPEC.md` §20, Phase 1 — Deliverable: "Monorepo, CI, compose, Caddy, empty API + web deployed". Acceptance: "Push to `main` deploys automatically; `/healthz` live over HTTPS; web on Vercel".

## Overview

The walking skeleton is the thinnest end-to-end slice of Disclosly that runs in production. It contains no
research features. Its purpose is to prove that a change merged to `main` passes every quality gate and
reaches real users automatically: the backend service is reachable over a secure public address, and the
web front end is published on its free hosting plan. Every later phase builds on this path.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Merge to main ships to production automatically (Priority: P1)

The maintainer opens a pull request, all required checks pass, and the pull request is squash-merged to
`main`. Without any manual step, the new backend version is deployed to the production server and the new
web version is published. The maintainer confirms the deployed version matches the merged commit.

**Why this priority**: This is the Phase 1 acceptance criterion and the foundation of every later phase.
Without an automatic, gated path to production, no later feature can be delivered or verified in production.

**Independent Test**: Merge a trivial change (for example, a version string bump) to `main`. Within the
target time, the public health check and the public web page both report the new commit identifier.

**Acceptance Scenarios**:

1. **Given** a pull request with all required checks green, **When** it is merged to `main`, **Then** the backend on the production server is replaced by the new version without manual action.
2. **Given** a pull request with all required checks green, **When** it is merged to `main`, **Then** the web front end is republished with the new version without manual action.
3. **Given** a pull request with a failing required check, **When** the maintainer tries to merge it, **Then** the merge is blocked.
4. **Given** a push directly to `main` (not via pull request), **When** it is attempted, **Then** it is rejected.
5. **Given** a deployment that fails its post-deploy health check, **When** the deploy job finishes, **Then** the job reports failure and the previous version keeps serving traffic.

---

### User Story 2 - Anyone can confirm the backend is alive over HTTPS (Priority: P1)

An operator, an uptime monitor or a visitor requests the public health check address of the backend over
HTTPS and receives a successful, machine-readable answer. A readiness check also reports whether the
service's dependencies are reachable.

**Why this priority**: "`/healthz` live over HTTPS" is an explicit acceptance criterion. It is the signal
that later monitoring, alerts and deploy checks rely on.

**Independent Test**: From any internet-connected machine, request the public health address over HTTPS;
receive a success status with a valid, trusted certificate. Request it over plain HTTP; get redirected to HTTPS.

**Acceptance Scenarios**:

1. **Given** the backend is deployed, **When** a client requests `/healthz` over HTTPS, **Then** it receives a success status and a small structured body containing service status and deployed version.
2. **Given** the backend is deployed, **When** a client requests the same address over plain HTTP, **Then** it is redirected to HTTPS.
3. **Given** the backend is deployed, **When** a client requests `/readyz`, **Then** it receives success only if every dependency the service needs is reachable, and a failure status naming the unavailable dependency otherwise.
4. **Given** any unknown path, **When** a client requests it, **Then** it receives a structured "not found" error with a stable error code and no stack trace.

---

### User Story 3 - Visitors can open the web front end (Priority: P2)

A visitor opens the public web address and sees a placeholder Disclosly page in Turkish by default, with
a switch to English. The page shows the product name, a one-line description, the disclaimer
"Yatırım tavsiyesi değildir / Not investment advice", and the backend status fetched live from the health check.

**Why this priority**: "Web on Vercel" is an acceptance criterion, and showing live backend status proves
the front end and backend are connected end to end. It ranks below Story 1 and 2 because it delivers no
research value yet.

**Independent Test**: Open the public web address on a desktop and a mobile browser; confirm the Turkish
page loads, the English toggle works, the disclaimer is visible, and the backend status indicator reflects
whether the backend is up.

**Acceptance Scenarios**:

1. **Given** the web front end is published, **When** a visitor opens the public address, **Then** the placeholder page loads in Turkish.
2. **Given** the placeholder page is open, **When** the visitor switches the language, **Then** all visible text appears in English.
3. **Given** the backend is up, **When** the page loads, **Then** it shows the backend as available with its version.
4. **Given** the backend is down or unreachable, **When** the page loads, **Then** it shows a friendly "service unavailable" message and does not break.

---

### User Story 4 - Contributor runs the whole stack locally with one command (Priority: P2)

A contributor clones the repository, copies the example environment file, and starts the full local stack
with one command. The local health check succeeds and the local web page shows the backend as available.

**Why this priority**: `PROJECT_SPEC.md` §19 requires "Local setup in one command", and a reproducible local
stack is what CI and production are built from. It ranks below the production path because the maintainer
can work without it for a short time.

**Independent Test**: On a clean machine with only the documented prerequisites, follow the README local
setup; the local health check returns success within the target time.

**Acceptance Scenarios**:

1. **Given** a fresh clone and a copied example environment file with placeholder values, **When** the contributor runs the documented single start command, **Then** all services start and the local health check succeeds.
2. **Given** the local stack is running, **When** the contributor runs the documented stop command, **Then** all services stop cleanly.

---

### User Story 5 - Every pull request is checked by the full quality gate (Priority: P1)

When a contributor opens or updates a pull request, CI runs every required check from the constitution's
Quality Gates on the skeleton code: lint and format, strict type checks, tests, coverage, evals, build, and
security scans. The pull request shows each check's result.

**Why this priority**: The constitution forbids merging without these gates. Setting them up while the
codebase is tiny is far cheaper than retrofitting later.

**Independent Test**: Open a pull request that introduces a deliberate lint error, a type error, and a
failing test in turn; each must turn its check red and block the merge.

**Acceptance Scenarios**:

1. **Given** a pull request, **When** CI runs, **Then** lint/format, type check, tests, coverage, evals, build and security checks each report pass or fail separately.
2. **Given** backend or frontend line coverage below 80%, **When** CI runs, **Then** the coverage check fails.
3. **Given** a committed secret-like value, **When** CI runs, **Then** the security check fails.
4. **Given** the skeleton has no LLM-dependent feature yet, **When** the eval check runs, **Then** it runs an empty or placeholder eval suite and passes, so the gate exists before the first real eval is added.

---

### Edge Cases

- Deployment succeeds but the new version fails its health check: the deploy reports failure and the previous version remains in service.
- Two merges land on `main` in quick succession: deployments run one at a time, and the final deployed version is the latest merged commit.
- The server reboots: all services restart automatically and the health check recovers without manual action.
- The TLS certificate approaches expiry: it renews automatically with no downtime.
- The web front end publishes but the backend deploy fails: the web page shows "service unavailable" instead of an error page.
- The web page is served from a different origin than the backend: the backend allows only the known web origins to call it from a browser.
- A required secret is missing from the deployment environment: the service fails to start with a clear message naming the missing setting (never its value), and the deploy job fails.
- Memory pressure on the server: each service stays within its configured memory limit; a service that exceeds it is restarted, not the whole server.

## Requirements *(mandatory)*

### Functional Requirements

**Repository and local development**

- **FR-001**: The repository MUST follow the top-level layout in `PROJECT_SPEC.md` §18, creating only the folders this phase uses (backend service, web front end, infrastructure, evals, docs, CI workflows). Empty future folders are not required.
- **FR-002**: The repository MUST include an example environment file with placeholder values only, and no real secrets anywhere in code, history, fixtures or logs.
- **FR-003**: A contributor MUST be able to start the complete local stack with one documented command and stop it with one documented command.
- **FR-004**: The README MUST contain a one-sentence product description, the local setup instructions, and links to the live backend health check and live web address. Other README sections from `PROJECT_SPEC.md` §19 are deferred to later phases.

**Backend service**

- **FR-005**: The backend MUST expose `/healthz` returning success with a structured body containing at least status and deployed version (commit identifier).
- **FR-006**: The backend MUST expose `/readyz` returning success only when all dependencies it is configured to use are reachable, and a failure status naming the unavailable dependency otherwise.
- **FR-007**: The backend MUST expose `/metrics` in a standard scrape-able metrics format with at least request count and latency per route.
- **FR-008**: The backend MUST return every error in one structured shape with a stable error code and a human-readable message; stack traces MUST NOT reach clients.
- **FR-009**: The backend MUST emit structured (JSON) logs for every request, containing route, status, latency and a request identifier, and MUST NOT log secrets or raw emails.
- **FR-010**: The backend MUST read all configuration from environment variables and MUST fail fast at startup with a clear message when a required setting is missing.
- **FR-011**: The backend MUST accept browser requests only from the configured web front end origins.

**Production deployment**

- **FR-012**: The backend MUST run on the single production server as a set of containers, each with an explicit memory limit within the budgets of `PROJECT_SPEC.md` §16.1.
- **FR-013**: A reverse proxy on the server MUST terminate HTTPS with an automatically obtained and renewed trusted certificate and MUST redirect plain HTTP to HTTPS.
- **FR-014**: The backend MUST be publicly reachable at `https://api.disclosly.com`. The owner has purchased the `disclosly.com` domain.
- **FR-015**: All services MUST restart automatically after a crash or server reboot.
- **FR-016**: The production server MUST have a swap file of 2–4 GB configured.
- **FR-017**: The production server (Hetzner Cloud), its firewall and the `disclosly.com` DNS records MUST be provisioned from infrastructure-as-code kept in the repository, per `PROJECT_SPEC.md` §16.2. Recreating the server from the repository MUST NOT need manual steps beyond supplying secrets.
- **FR-018**: The server firewall MUST allow only HTTPS, HTTP (for redirect and certificate issuance) and administrative SSH; database and cache ports MUST NOT be publicly reachable.

**Continuous integration and delivery**

- **FR-019**: CI MUST run on every pull request and on every push to `main`, with each constitution Quality Gate (lint & format, type check, tests, coverage ≥ 80% backend and frontend separately, evals, build, security) reported as a separate required check.
- **FR-020**: The eval gate MUST exist and run in CI using recorded responses only; with no LLM features yet, it MUST pass on an empty or placeholder suite.
- **FR-021**: On every push to `main` that passes CI, the pipeline MUST build the backend container images, publish them to a container registry, and deploy them to the production server without manual action.
- **FR-022**: Each deployment MUST verify the public health check after rollout and MUST report failure, leaving the previous version serving, if the check does not pass.
- **FR-023**: Deployments MUST run one at a time; a newer deploy MUST NOT run concurrently with an older one.
- **FR-024**: Deployment credentials (server access, registry access) MUST be stored only in the CI secret store.
- **FR-025**: The `main` branch MUST be protected: pull request required, required checks must pass, squash merge only, no direct or force pushes.
- **FR-026**: Automated dependency update pull requests MUST be enabled for backend, frontend, container images and CI workflow dependencies.

**Web front end**

- **FR-027**: The web front end MUST be published on the free Vercel plan and republished automatically on every push to `main`.
- **FR-028**: The web front end MUST show a placeholder page with product name, one-line description, the disclaimer "Yatırım tavsiyesi değildir / Not investment advice", and live backend status from the health check.
- **FR-029**: The web front end MUST default to Turkish and offer a switch to English; all visible text MUST be available in both languages.
- **FR-030**: The web front end MUST be usable on mobile screen widths.
- **FR-031**: The web front end MUST include at least one automated browser smoke test that loads the page and checks the disclaimer is visible.

**Repository policy files**

- **FR-032**: The repository MUST include the policy files the constitution lists as follow-ups: LICENSE, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, CODEOWNERS, pull request template (with the constitution checklist) and issue templates.

### Key Entities

- **Deployment**: One rollout of a merged commit to production. Attributes: commit identifier, start and end time, result (succeeded / failed / rolled back), health check result.
- **Health status**: What `/healthz` and `/readyz` report. Attributes: overall status, deployed version, per-dependency status (for readiness).
- **Environment configuration**: The set of named settings each service needs. Attributes: name, required or optional, secret or not, placeholder value in the example file. Real values live only in the deployment environment and CI secret store.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a pull request is merged to `main`, the new version is live on both the backend health check and the web page within 15 minutes, with zero manual steps.
- **SC-002**: The public health check answers successfully over HTTPS with a certificate that standard browsers trust, from at least two different networks.
- **SC-003**: Plain HTTP requests to the backend are redirected to HTTPS in 100% of tested cases.
- **SC-004**: A pull request with a deliberate defect in each gate category (lint, type, test, coverage, security) is blocked from merging in 100% of attempts.
- **SC-005**: A new contributor following only the README reaches a successful local health check within 10 minutes on a machine with the prerequisites installed (excluding first-time image downloads).
- **SC-006**: A deliberately broken deploy (new version fails health check) leaves the previous version serving, with no more than 1 minute of failed health checks.
- **SC-007**: After a server reboot, the public health check recovers without manual action within 5 minutes.
- **SC-008**: Measured idle memory of all production services combined stays under 2 GB, and each service stays under its configured limit; the measured numbers are recorded in the README.
- **SC-009**: The placeholder web page loads and shows the disclaimer on a mobile browser and a desktop browser in both Turkish and English.

## Assumptions

- **Scope of "empty API"**: The backend contains only health, readiness, metrics and error handling. No research endpoints, authentication, quotas or LLM calls are in scope; these arrive in Phases 2–3.
- **Data stores**: The local and production stacks include the database and cache services from `PROJECT_SPEC.md` §16.1 so that `/readyz` checks real dependencies and later phases only add schema, not infrastructure. No application tables or migrations beyond an initial empty migration baseline are created.
- **Services not in Phase 1**: Background worker, MCP server, sandbox and telemetry collector containers are deferred to the phases that use them. Request tracing to external observability services (Langfuse, Grafana Cloud, Sentry) is deferred to Phase 5; Phase 1 provides structured logs and the metrics endpoint only.
- **Web hosting**: Vercel free plan is used, as the Phase 1 acceptance criterion names it (Cloudflare Pages is not used). The web page is served at `https://disclosly.com`, with `www` redirecting to it.
- **DNS**: `disclosly.com` DNS is hosted on Cloudflare's free plan, as `PROJECT_SPEC.md` §16.2 states, and its records are managed by the infrastructure-as-code from FR-017.
- **Server size**: The Hetzner server matches `PROJECT_SPEC.md` §2 (2 vCPU, 4 GB RAM, 40 GB disk); the server cost is outside the $0 LLM budget.
- **Container registry**: A free registry tied to the public GitHub repository is used, so no new paid service is needed.
- **Bot protection, analytics, auth, privacy and terms pages**: Out of scope for Phase 1; they arrive in Phase 3 or later.
- **Backups and alerts**: Nightly database backups and alerting are out of scope; there is no user data in Phase 1.
- **Zero-downtime**: "Zero-downtime-ish" deploys (per `PROJECT_SPEC.md` §16.2) are acceptable; brief interruptions of a few seconds during restart are tolerated.
- **Repository visibility**: The GitHub repository is public, as `PROJECT_SPEC.md` §2 states, so GitHub Actions minutes and security scans are free.
- **Dependencies**: Every dependency and service used is already listed in `PROJECT_SPEC.md` §4 or §16; any other addition needs owner approval before planning.
