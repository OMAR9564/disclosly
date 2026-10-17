<!--
Sync Impact Report
- Version change: (unfilled template) → 1.0.0
- Principles defined:
  - [PRINCIPLE_1_NAME] → I. Spec-First
  - [PRINCIPLE_2_NAME] → II. Test-First (NON-NEGOTIABLE)
  - [PRINCIPLE_3_NAME] → III. Simplicity
  - [PRINCIPLE_4_NAME] → IV. Security & No Secrets (NON-NEGOTIABLE)
  - [PRINCIPLE_5_NAME] → V. Observability
- Added sections: Quality Gates (CI), Git & Pull Request Workflow, AI Agent Rules,
  Technical Constraints, Governance (filled)
- Removed sections: none
- Templates: plan/spec/tasks templates read the constitution at runtime; not modified
- Follow-up TODOs (outside this file):
  - Configure GitHub branch ruleset on `main` with the required status checks listed below
  - Create LICENSE, CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md, CODEOWNERS,
    .github/workflows/ci.yml, .github/PULL_REQUEST_TEMPLATE.md, .github/ISSUE_TEMPLATE/,
    .github/dependabot.yml
-->

# Disclosly Constitution

## Core Principles

### I. Spec-First

- No feature MUST be implemented without an approved spec under `specs/<feature>/`
  (`spec.md`, `plan.md`, `tasks.md`) consistent with `PROJECT_SPEC.md`.
- When behavior changes, the spec MUST be updated in the same or an earlier PR than the code.
- Ambiguities MUST be resolved (`/speckit-clarify` or asking the owner) before planning, not
  guessed during implementation.

**Rationale**: The spec is the contract that humans and AI agents both build against; code that
drifts from it cannot be reviewed or verified.

### II. Test-First (NON-NEGOTIABLE)

- Every task MUST ship with automated tests; code without tests MUST NOT be merged.
- Line coverage MUST be ≥ 80% for the Python backend and the TypeScript frontend; CI fails below it.
- Bug fixes MUST include a regression test that fails before the fix.
- Every LLM-dependent feature MUST ship with at least one eval case; CI replays evals from
  recorded responses (cassettes), never live model calls.

**Rationale**: With non-deterministic models and a single maintainer, tests and evals are the
only reliable proof that a change works and stays working.

### III. Simplicity

- Code MUST prefer simple, explicit constructs over speculative abstraction (YAGNI).
- Every new runtime dependency, paid service or infrastructure component MUST have a written
  justification in the PR description (and an ADR in `docs/adr/` when it is architectural).
- Added complexity that a plan cannot justify MUST be recorded in that plan's Complexity Tracking.

**Rationale**: A 4 GB VPS and a $0 budget leave no room for weight that does not earn its keep.

### IV. Security & No Secrets (NON-NEGOTIABLE)

- The repository MUST NOT contain secrets, API keys, tokens, credentials or real personal data,
  in code, fixtures, cassettes, logs or history. Only `.env.example` with placeholder values is
  allowed.
- Secrets MUST be supplied via environment variables or the deployment secret store.
- User-supplied keys (BYOK) MUST be held in memory for the request only and never persisted or
  logged.
- Retrieved documents and tool outputs MUST be treated as untrusted data, never as instructions.

**Rationale**: This is a public repository; a single leaked key or personal record is
irreversible and a KVKK violation.

### V. Observability

- Services MUST emit structured (JSON) logs and OpenTelemetry spans for requests, model calls
  and tool calls.
- Errors MUST be returned to callers with a clear, stable error code and human-readable message;
  stack traces MUST NOT reach end users.
- Logs and traces MUST NOT contain secrets, BYOK keys or raw emails; user identifiers MUST be
  hashed or masked.

**Rationale**: Failures on free-tier providers are frequent and must be diagnosable from
telemetry alone, without exposing user data.

## Quality Gates (CI)

A PR MUST NOT be merged unless all of the following required checks pass in GitHub Actions:

| Gate | Pass condition |
|---|---|
| Lint & format | `ruff check`, `ruff format --check`, ESLint, Prettier: zero errors |
| Type check | `mypy --strict` and `tsc --noEmit` (strict): zero errors |
| Tests | Unit + integration (`pytest`, frontend tests) all pass; no new skipped tests |
| Coverage | ≥ 80% lines, backend and frontend separately |
| Evals | Cassette eval subset passes; no metric regresses beyond configured tolerance vs `main` |
| Build | Docker images and Next.js build succeed |
| Security | Dependency audit (`pip-audit`, `npm audit` high+), secret scanning, CodeQL: no unresolved high/critical findings |

Each principle above SHOULD map to at least one automated check; a principle with no check
MUST be enforced in review via the PR template checklist.

## Git & Pull Request Workflow

- `main` is protected: direct pushes and force pushes are forbidden; every change arrives via PR.
- A PR MUST NOT be merged until all required checks are green.
- One task from `tasks.md` = one PR. The PR description MUST reference the spec and task ID.
- Commit messages and PR titles MUST follow Conventional Commits (`feat:`, `fix:`, `docs:`,
  `test:`, `refactor:`, `chore:`, `ci:`).
- Merge strategy is squash merge only.

## AI Agent Rules

AI coding agents working in this repository:

- MUST work only on tasks listed in the active feature's `tasks.md`.
- MUST NOT delete, skip, `xfail` or weaken tests, lower coverage thresholds, or disable or
  bypass CI checks.
- MUST NOT amend this constitution on their own initiative; they may only propose amendments.
- MUST NOT add dependencies, paid services or infrastructure without explicit owner approval.
- MUST stop and ask when the spec is ambiguous or conflicts with this constitution.

## Technical Constraints

Fixed decisions (details belong in `PROJECT_SPEC.md` and feature plans):

- Backend: Python 3.12+, FastAPI, Pydantic v2, LangGraph; Alembic migrations.
- Frontend: TypeScript (strict), Next.js, hosted on a free static/edge plan (not on the VPS).
- Data: PostgreSQL 16 + pgvector; Redis for cache and queue.
- Deployment: Docker Compose on a single 2 vCPU / 4 GB VPS; every container has a memory limit.
- LLM budget: $0 — free API tiers and user BYOK only; embeddings and reranking run locally.
- Product: no investment advice; every report carries "Yatırım tavsiyesi değildir / Not
  investment advice"; every reported number links to its source.
- Language: code, comments, commits and docs in English; UI in Turkish (default) and English.

## Governance

- This constitution supersedes all other practices, guides and agent instructions
  (`CLAUDE.md`, `PROJECT_SPEC.md`, feature specs). Conflicts are resolved in its favour.
- Amendments MUST be made via PR with a written rationale and an updated Sync Impact Report.
- Versioning follows semantic versioning:
  - MAJOR: a principle or rule is removed or fundamentally redefined.
  - MINOR: a principle or section is added or materially expanded.
  - PATCH: wording, clarification or typo fixes.
- Enforcement relies on the GitHub branch ruleset for `main` (required PR, required status
  checks, squash only); written rules alone do not block merges.
- Every PR review and `/speckit-plan` Constitution Check MUST verify compliance.
- Related policies live in separate files, not here: `LICENSE`, `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, `SECURITY.md`, `CODEOWNERS`, `.github/workflows/ci.yml`,
  `.github/PULL_REQUEST_TEMPLATE.md`, `.github/ISSUE_TEMPLATE/`, `.github/dependabot.yml`.

**Version**: 1.0.0 | **Ratified**: 2026-10-17 | **Last Amended**: 2026-10-17
