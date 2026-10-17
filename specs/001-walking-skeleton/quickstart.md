# Quickstart & Validation: Phase 1 — Walking Skeleton

Use this guide to prove the feature works end to end. Each scenario maps to spec stories, FRs and
SCs. Contracts: [openapi.yaml](./contracts/openapi.yaml), [configuration.md](./contracts/configuration.md),
[web-page.md](./contracts/web-page.md).

## Prerequisites

- Linux or macOS with Docker Engine 24+ and the Compose plugin, `uv`, Node.js 24 LTS, and `make`.
- For infrastructure only: Terraform ≥ 1.9, a Hetzner Cloud project token, and a Cloudflare API
  token scoped to `disclosly.com`.

## V1 — Local stack in one command (Story 4, FR-003, SC-005)

```bash
cp .env.example .env
```

```bash
make up
```

```bash
curl -s localhost:8000/healthz
```

Expected: `{"status":"ok","version":"dev","service":"disclosly-api"}`.

```bash
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/readyz
```

Expected: `200`. Run `docker compose stop redis`, then check again. Expected: `503`, and the body
shows `redis.status = error`.

```bash
make web
```

Open `http://localhost:3000`. Expected: Turkish page, disclaimer visible, status
`data-state="available"`. Run `make down` to stop everything.

## V2 — Local quality gates (Story 5, FR-019, FR-020)

```bash
make lint typecheck test
```

```bash
uv run python -m evals.run --cassettes-only
```

Expected: all pass. Coverage is reported at ≥ 80% for both backend and web. The eval runner
reports `0 cases, passed`.

## V3 — Error contract (Story 2, FR-008)

```bash
curl -s -i localhost:8000/does-not-exist
```

Expected: `404`, an `X-Request-ID` header, a body matching `ErrorEnvelope` with
`code=not_found`, and no stack trace.

## V4 — Provision infrastructure (FR-016, FR-017, FR-018)

```bash
terraform -chdir=infra/terraform init
```

```bash
terraform -chdir=infra/terraform apply
```

Expected: one server, a firewall with only 22/80/443 inbound, and DNS records for `api`, the apex
and `www`. On the server: `swapon --show` lists a 4 GB swap file, and SSH password login is
refused. `nc -z <server-ip> 5432` and `nc -z <server-ip> 6379` both fail (ports not public).

## V5 — Gated pipeline (Story 1 & 5, FR-021–FR-025, SC-001, SC-004)

1. Open a PR that only changes a comment. All required checks pass. Merge it (squash).
2. Within 15 minutes, both of these show the merge commit SHA:
   ```bash
   curl -s https://api.disclosly.com/healthz
   ```
   and the status line on `https://disclosly.com`.
3. Open separate throwaway PRs that each add one of the following: a ruff violation, a mypy
   error, a failing test, a coverage drop below 80%, and a fake secret (a test pattern gitleaks
   detects, not a real key). Each PR shows its matching check red and cannot be merged. Close
   them without merging.
4. Try `git push origin main` directly. Expected: rejected by the branch ruleset.

## V6 — HTTPS and redirect (Story 2, FR-013, SC-002, SC-003)

```bash
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://api.disclosly.com/healthz
```

Expected: `301` or `308` to `https://api.disclosly.com/healthz`. Then:

```bash
curl -sv https://api.disclosly.com/healthz 2>&1 | grep -E 'SSL certificate verify ok|HTTP/'
```

Expected: certificate verified, `200`. Repeat from a second network, for example a phone hotspot.

## V7 — Failed deploy rolls back (FR-022, SC-006)

On a throwaway branch, make `/healthz` return 500, merge it through a PR, and watch `deploy.yml`.
Expected: the deploy job fails at the health check step, the rollback step runs, and
`/healthz` still reports the previous SHA. Fewer than 1 minute of failed checks are seen. Then
revert the change through a PR.

## V8 — Reboot recovery and memory (FR-015, SC-007, SC-008)

```bash
ssh deploy@<server> 'sudo reboot'
```

Expected: `https://api.disclosly.com/healthz` returns `200` again within 5 minutes with no manual
action. Then:

```bash
ssh deploy@<server> 'docker stats --no-stream'
```

Expected: every container is below its limit, and the total is below 2 GB. Record the numbers in
the README.

## V9 — Web page (Story 3, FR-027–FR-031, SC-009)

On a desktop browser and a phone, open `https://disclosly.com`. Check that:
- the page is Turkish by default and the disclaimer is visible;
- the toggle switches the page to English and the choice persists after a reload;
- the status shows `available` with the SHA;
- with the API stopped (`docker compose stop api` on the server, then started again), the status
  shows the friendly `unavailable` text;
- `https://www.disclosly.com` redirects to the apex.
