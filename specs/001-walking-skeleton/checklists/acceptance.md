# Acceptance: 001 walking skeleton

Results of quickstart V1–V9 against SC-001 to SC-009 (tasks.md T048). All nine criteria pass,
except SC-006, which is an accepted deviation (about 64 s of failed checks against a 1-minute
target).

| Criterion | Check | Result | Evidence | Date |
|---|---|---|---|---|
| SC-001 merge to live < 15 min | V5 steps 1–2 | pass | PR #19: CI started 20:02, `/healthz` reported `130a0b5` about 2 min later | 2026-10-18 |
| SC-002 HTTPS with trusted certificate from two networks | V4 | pass | maintainer network: `/healthz` 200, `ssl_verify_result=0`; mobile data network: JSON shown with no certificate warning (owner check) | 2026-10-19 |
| SC-003 HTTP redirects to HTTPS | V4 | pass | `http://api.disclosly.com/healthz` returns 308 with `Location: https://api.disclosly.com/healthz` | 2026-10-19 |
| SC-004 each defective gate blocks the merge | V5 steps 3–4 | pass | owner ran the runbook throwaway PRs (lint, typecheck, test, coverage, gitleaks): each check turned red and blocked the merge; direct push to `main` rejected (Phase 4) | 2026-10-19 |
| SC-005 README to local health check < 10 min | V1 | pass | clean clone in `/tmp` by the owner: `make up` and `/healthz` succeeded (duration not recorded). Earlier, in the working copy with images cached: `make up` ready in 20 s, `/healthz` 200, `/readyz` 200, then 503 with `redis.status=error` after stopping redis; CI runs the same `make up` smoke test | 2026-10-19 |
| SC-006 broken deploy keeps the previous version, < 1 min failed checks | V7 | accepted deviation | PR #20: rollback to `130a0b5` worked; failed checks lasted about 64 s (runbook "Rollback", ADR 0001) | 2026-10-18 |
| SC-007 reboot recovery < 5 min | V8 | pass | Hetzner Shutdown then Power on: `/healthz` back 26 s after power-on; `uptime -p` 0 minutes, all four services up, postgres/redis/api healthy, no manual step | 2026-10-19 |
| SC-008 idle memory < 2 GB total, each service under its limit | V8 | pass | `docker stats --no-stream`: api 133.7/512 MiB, postgres 31.1 MiB/1 GiB, caddy 12.8/64 MiB, redis 3.2/160 MiB, total about 181 MiB (README.md) | 2026-10-19 |
| SC-009 page on mobile and desktop, TR and EN, disclaimer visible | V9 | pass | owner check after PR #24 (T042) | 2026-10-19 |
