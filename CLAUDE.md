# Disclosly — Agent Guide

Multi-agent research platform over company disclosures (KAP + SEC EDGAR), cited answers, $0 LLM budget.

## Sources of truth (read in this order)
1. `.specify/memory/constitution.md` — project constitution. Overrides everything else, including this file.
2. `PROJECT_SPEC.md` — full product/technical spec (architecture, stack, constraints, phases, definition of done).
3. `specs/<feature>/` — Spec Kit artifacts for the active feature (`spec.md`, `plan.md`, `tasks.md`).

## Workflow: Spec Kit
All work goes through Spec Kit, in this order:
`/speckit-constitution` → `/speckit-specify` → `/speckit-clarify` → `/speckit-plan` → `/speckit-tasks` → `/speckit-analyze` → `/speckit-implement`

- Features follow the phases in `PROJECT_SPEC.md` §20; do not start a phase before the previous one's acceptance criteria pass.
- Only work on tasks listed in the active feature's `tasks.md`.
- If behavior changes, update the spec first, then the code.

## Hard rules
- Never run `git commit` (or push, merge, amend). Only propose commit messages; the owner commits.
- Never delete, skip or weaken tests; never disable CI.
- Never add a dependency, paid service or infra component without asking first.
- Never edit the constitution on your own.
- No secrets, tokens or real personal data in the repo; only `.env.example`.
- When the spec is ambiguous, stop and ask. Open decisions are listed in `PROJECT_SPEC.md` §22.

## Conventions
- Code, comments, commits and docs in English. Product UI: Turkish (default) + English.
- Python 3.12+, typed (mypy strict, Pydantic v2); TypeScript strict.
- Conventional Commits; one task per PR; squash merge; `main` is protected.
