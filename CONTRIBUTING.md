# Contributing to Disclosly

Thanks for helping out.

## Spec first

Every change starts from a spec. We use [Spec Kit](https://github.com/github/spec-kit) in this order:

1. `/speckit-specify` writes `specs/<feature>/spec.md`.
2. `/speckit-clarify` settles open questions.
3. `/speckit-plan` writes `plan.md` and its design files.
4. `/speckit-tasks` writes `tasks.md`.
5. `/speckit-analyze` checks the three files against each other.
6. `/speckit-implement` builds the tasks.

The project constitution in `.specify/memory/constitution.md` wins over every other document. If
behavior changes, update the spec in the same PR or an earlier one, then the code.

## One task, one pull request

- Pick one task from the active feature's `tasks.md`. Do not mix tasks in a PR.
- Write the tests first, watch them fail, then make them pass.
- Reference the spec and task ID in the PR description (the template asks for both).
- PRs are squash-merged. `main` is protected and only accepts PRs with green checks.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`,
`test:`, `refactor:`, `chore:`, `ci:`. The PR title follows the same format because it becomes the
squash commit.

## Local gates

Run these before you open a PR. CI runs the same checks.

```bash
make lint
make typecheck
make test
```

Coverage must stay at or above 80% lines for the backend and for the web app, measured separately.
Never skip, delete or weaken a test to get a green build.

## Dependencies

Adding a runtime dependency, paid service or infrastructure component needs the maintainer's
approval first. Write the reason in the PR description, and add an ADR under `docs/adr/` when the
choice is architectural.

## Secrets and data

Never commit secrets, tokens or real personal data. Only `.env.example` with placeholder values
belongs in the repository. Raw KAP or SEC filings do not go into the repository either.
