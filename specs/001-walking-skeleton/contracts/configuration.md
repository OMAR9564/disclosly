# Configuration Contract: Phase 1

All values come from environment variables. `.env.example` lists every variable with a placeholder.
Real values live in the server `~/disclosly/.env` (mode 600), in GitHub Actions secrets, or in the
maintainer's shell (Terraform). None are committed.

## API container

| Variable | Required | Secret | Example placeholder | Notes |
|---|---|---|---|---|
| `APP_ENV` | yes | no | `development` | `development` \| `test` \| `production` |
| `APP_VERSION` | no | no | `dev` | Baked into the image at build time (git SHA). |
| `DATABASE_URL` | yes | yes | `postgresql://disclosly:CHANGE_ME@postgres:5432/disclosly` | psycopg 3 DSN. |
| `REDIS_URL` | yes | no | `redis://redis:6379/0` | |
| `CORS_ALLOWED_ORIGINS` | yes | no | `http://localhost:3000` | Production: `https://disclosly.com,https://www.disclosly.com` |
| `LOG_LEVEL` | no | no | `INFO` | |
| `PROMETHEUS_MULTIPROC_DIR` | yes in container | no | `/tmp/prometheus` | Set in Dockerfile; tmpfs. |

## Postgres / Redis containers

| Variable | Required | Secret | Example placeholder |
|---|---|---|---|
| `POSTGRES_USER` | yes | no | `disclosly` |
| `POSTGRES_PASSWORD` | yes | yes | `CHANGE_ME` |
| `POSTGRES_DB` | yes | no | `disclosly` |

## Compose / Caddy (server `.env`)

| Variable | Required | Secret | Example placeholder | Notes |
|---|---|---|---|---|
| `API_IMAGE_TAG` | yes | no | `main` | Written by `deploy.sh`. |
| `API_DOMAIN` | yes | no | `api.disclosly.com` | Caddy site address. |
| `ACME_EMAIL` | yes | no | `ops@example.com` | Let's Encrypt account contact. Use a role address, not a personal one. |

## Web (Vercel project env / build time)

| Variable | Required | Secret | Example placeholder |
|---|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | yes | no | `http://localhost:8000` (prod: `https://api.disclosly.com`) |
| `NEXT_PUBLIC_APP_VERSION` | no | no | `dev` (CI sets git SHA) |

## GitHub Actions secrets

| Secret | Used by |
|---|---|
| `DEPLOY_SSH_KEY` | deploy.yml: SSH to `deploy@<server>` (ed25519, deploy-only key) |
| `DEPLOY_HOST` | deploy.yml: server IP / hostname |
| `DEPLOY_KNOWN_HOSTS` | deploy.yml: pinned host key (no `StrictHostKeyChecking=no`) |
| `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` | deploy.yml: web deploy |

GHCR push uses the built-in `GITHUB_TOKEN`. The server pulls public images anonymously.

## Terraform (maintainer shell only)

| Variable | Secret | Notes |
|---|---|---|
| `HCLOUD_TOKEN` | yes | Hetzner project token, read+write |
| `CLOUDFLARE_API_TOKEN` | yes | Scoped to the `disclosly.com` zone: DNS edit + zone settings edit |
| `TF_VAR_ssh_public_key` | no | Maintainer and deploy public keys |
| `TF_VAR_server_type`, `TF_VAR_location` | no | Defaults in `variables.tf` |
