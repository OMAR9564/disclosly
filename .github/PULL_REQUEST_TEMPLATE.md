## Summary

<!-- What changes and why, in one or two sentences. -->

## Spec and task

- Spec: `specs/<feature>/spec.md`
- Task: `T0XX`

## Constitution checklist

- [ ] **I. Spec-First**: the change matches an approved spec; any behavior change updated the spec first.
- [ ] **II. Test-First**: tests were written first, they pass, and coverage stays at or above 80%.
- [ ] **III. Simplicity**: no speculative abstraction; every new dependency is justified below.
- [ ] **IV. Security & No Secrets**: no secrets, tokens or real personal data in code, fixtures or history.
- [ ] **V. Observability**: logs are structured, errors use stable codes, and nothing sensitive is logged.

## Quality gates

- [ ] Lint and format (`ruff`, ESLint, Prettier)
- [ ] Type check (`mypy --strict`, `tsc --noEmit`)
- [ ] Tests (unit and integration, nothing skipped)
- [ ] Coverage at or above 80%, backend and web separately
- [ ] Evals (cassette suite)
- [ ] Build (Docker image and Next.js)
- [ ] Security (`pip-audit`, `npm audit`, secret scan, CodeQL)

## New dependencies

<!-- Name each one with its justification, or write "None". -->
