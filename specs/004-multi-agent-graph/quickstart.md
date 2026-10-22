# Quickstart: Multi-Agent Research validation

How to prove Phase 4 works. Contracts: [contracts/](contracts/); data: [data-model.md](data-model.md).

## Prerequisites

- Phase 3 running (local `make up` or production) with the corpus loaded and at least two
  provider keys.
- Docker available to the `sandbox` service (local: the dev compose mounts
  `/var/run/docker.sock`; the sandbox image is built by `make up`).
- New variables in `.env` (see `.env.example`): `AGENT_ENGINE=graph`, `QUOTA_DAILY_DEEP=5`,
  `AGENT_DEEP_MAX_MODEL_CALLS=20`, `AGENT_DEEP_DEADLINE_SECONDS=300`, `SANDBOX_URL`,
  `SANDBOX_IMAGE_TAG`. `LANGSMITH_TRACING` must be unset.

## 1. Offline checks (CI equivalent)

```bash
uv run pytest tests/unit
uv run pytest -m integration tests/integration
uv run pytest -m sandbox tests/integration/sandbox
uv run python -m evals.run agent --replay
uv run python -m evals.run graph --replay
```

Expected: all pass. The graph eval report shows citation coverage 100%, derived numbers from
calculations 100%, numeric match ≥ 80% on `deep_golden` (SC-002), seeded verifier rejection
≥ 90% (SC-003), refusal and injection 100% (SC-004), simple subset not below the Phase 3
baseline beyond tolerance (SC-007). The sandbox suite blocks every escape case (SC-008).

## 2. Restart resume (US3, SC-001)

Automated: `uv run pytest -m integration tests/integration/agent/test_resume.py`.

Manual, local or production:

1. Ask a deep question ("Compare Apple's and Microsoft's gross margin over the last 4
   quarters"), approve the plan.
2. While `step_started` events arrive, run `docker compose restart api` (production: the deploy
   workflow does the same).
3. Reload the run page. Expected: a `run_resumed` event, the remaining steps, a final report;
   `GET /v1/research/{id}` shows `usage.resumes = 1`; `GET /v1/me` shows one deep unit used.
4. Repeat with a run waiting for approval: after the restart the plan is still shown and
   approving it continues the run.

## 3. Plan approval (US1)

- Ask "THYAO'nun son 4 çeyrekteki net borç / FAVÖK oranı nasıl değişti?" → plan within 15 s
  with estimate and remaining deep quota (SC-006).
- Edit: remove a step and add one for PGSUS → the run follows the edited plan.
- Edit with 9 steps → `plan_invalid`, plan stays open.
- Cancel another plan → status `cancelled`, no `model_called` events after the decision; the
  simple quota went down by one, the deep quota did not.
- Approve from two tabs at once → one succeeds, the other gets `already_decided`.

## 4. Report and calculations (US2)

On the approved THYAO run: the report has a table, a line chart whose points equal the table's
ratio column, a confidence note and the disclaimer. Opening the ratio's citation shows both
inputs with their own source excerpts and the formula.

## 5. Simple questions (US4)

"Akbank'ın 2026 üçüncü çeyrek net kârı ne kadardı?" → no plan, answer within 30 s, at most 4
`model_called` events.

## 6. Memory and settings page (US5)

- `/settings`: add ASELS, THYAO, KCHOL; set default period to last 8 quarters; reload → saved.
- Ask "Takip listemdeki şirketlerin net kâr marjlarını karşılaştır" → plan covers exactly those
  three; report says it used the watchlist and the default period.
- Ask "ASELS'i takip listemden çıkar" → `memory_updated`; settings page shows two companies.
- Try to add an unknown ticker → `company_not_covered`.
- Delete all memory → a watchlist question answers that the watchlist is empty.

## 7. Quotas

Set `QUOTA_DAILY_DEEP=1`, approve one deep plan, ask another deep question → the run offers
simple mode before any planner call; choosing simple answers it in simple mode.

## 8. Comparison (US6, SC-010)

```bash
uv run python -m evals.run compare --replay
```

Expected: `evals/reports/<date>-single-vs-multi.md` with both engines' metrics on the same
cases; `docs/evals/single-vs-multi.md` and the README table updated.

## 9. Memory footprint

After a deep run on production: `ssh deploy@<server> 'docker stats --no-stream'`. Record
`api`, `sandbox` and peak sandbox container memory in the README table.
