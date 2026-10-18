# ADR 0001: Walking-skeleton delivery path

- Status: accepted
- Date: 2026-10-19
- Source: `specs/001-walking-skeleton/research.md` R7 to R10

## Context

Phase 1 has to put a web page and an API in production with no manual deploy step, on one
4 GB server, on free plans, and with a single maintainer. Every later phase ships through
this path, so it must be cheap to run and simple to debug, and a failed release must roll back.

## Decisions

### 1. Deploy and rollback: SSH plus a shell script (R7)

`deploy.yml` runs after `CI` succeeds on `main`. It pushes the API image to GHCR, then runs
`infra/scripts/deploy.sh <sha>` on the server over SSH. The script stores the current tag,
pulls the new image, runs migrations, and restarts only the `api` container. It then waits up to
60 s for `/healthz` to report the new SHA. If the check fails, the script restores the previous
tag and exits non-zero, so the deploy job turns red.

The server keeps no hand-edited configuration. The Compose file and the Caddyfile are copied on
every deploy, and only `.env` lives on the server.

Alternatives considered:

- Watchtower or another pull-based updater. It needs no SSH secret, but a failure does not show up
  in CI, and it has no ordered rollback.
- Kamal or Docker Swarm. Either adds a tool or an orchestrator for a single server.
- Blue/green API containers. Not needed at this scale; a few seconds of restart downtime are
  accepted.

Consequence: the rollback drill on 2026-10-18 measured about 64 s of failed checks, slightly
over the 1-minute target, because the script waits out the full 60 s even when `/healthz`
returns 5xx. This was accepted. Rolling back after a few consecutive 5xx responses would cut it
to about 15 s.

### 2. TLS: Cloudflare Full (strict) in front of Caddy with HTTP-01 (R8)

Cloudflare hosts DNS. `api.disclosly.com` is a proxied record, and the zone uses SSL mode
Full (strict). Caddy gets and renews a Let's Encrypt certificate with the HTTP-01 challenge
through the proxy, and it does the HTTP-to-HTTPS redirect itself ("Always Use HTTPS" is off in
Cloudflare, so the challenge can reach it).

Alternatives considered:

- Cloudflare Origin CA certificate. It works only while traffic is proxied, and its private key
  has to be handled by hand.
- DNS-01 challenge. It needs a custom Caddy build with the Cloudflare plugin and an API token on
  the server. It stays the fallback if HTTP-01 through the proxy becomes unreliable.

### 3. Infrastructure: Terraform from the laptop with local state (R9)

Terraform manages the Hetzner server, firewall and SSH key, plus the Cloudflare DNS records and
SSL settings. `cloud-init.yaml` prepares the server. Terraform runs from the maintainer's laptop.
The state file is local, gitignored, and backed up by the owner. Provider tokens come from
environment variables and are never written to a file.

Alternatives considered:

- Terraform Cloud for remote state. It is another account and service to approve.
- S3-compatible state on Hetzner Object Storage. It costs money.
- `terraform apply` from CI. It puts high-privilege provider tokens into GitHub.

Consequence: losing the laptop without a state backup means importing resources again. The
runbook covers the backup.

### 4. Web: Vercel, deployed from CI only (R10)

The Next.js app is hosted on Vercel's free plan. `web/vercel.json` turns off Vercel's Git
deploys, and `deploy.yml` builds and publishes with the pinned Vercel CLI, using a token from
Actions secrets. The web app ships through the same CI gate as the API.

Alternatives considered:

- Vercel Git auto-deploy. It is simpler, but it publishes even when CI fails.
- Cloudflare Pages. The Phase 1 acceptance criterion names Vercel.

## Consequences

- One person can run, debug and rebuild the whole path with SSH, shell, Docker Compose and
  Terraform. There is no orchestrator to learn.
- Deploys are ordered, gated by CI, and visible in GitHub Actions. A failed health check rolls
  back automatically.
- GitHub holds the secrets for deploying (SSH key, Vercel token) but not the infrastructure
  tokens.
- A restart costs a few seconds of API downtime. Zero-downtime deploys are a later decision.
