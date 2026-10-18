# Runbook

Operational procedures for the production stack: one Hetzner server behind Cloudflare, serving
`api.disclosly.com`. Design decisions are in `specs/001-walking-skeleton/research.md` (R7 deploy
and rollback, R8 TLS, R9 Terraform). Commands marked **laptop** run in the repository root on the
maintainer's machine; commands marked **server** run as `deploy` on the server.

## Provisioning

Done once, or again for a full rebuild. Terraform state is local
(`infra/terraform/terraform.tfstate`, gitignored); back it up after every `apply`.

### 1. Cloudflare zone

Add `disclosly.com` to Cloudflare and point the registrar's nameservers to the two Cloudflare
nameservers. Wait until the zone shows **Active**.

Remove any records Cloudflare imported for the apex, `www` or `api`; Terraform creates them and
fails with "record already exists" otherwise. Leave MX and TXT records alone.

### 2. Provider tokens

Neither token is ever written to a file. Export them in the shell that runs Terraform.

- **Cloudflare**: a **user** API token (My Profile → API Tokens → Create Custom Token), zone
  resources limited to `disclosly.com`, with:
  - Zone → Zone → Read
  - Zone → DNS → Edit
  - Zone → Zone Settings → Edit
  - Zone → SSL and Certificates → Edit

  An account-owned token (Manage account → Account API tokens) with the same permissions created
  the DNS records but got `403 / 9109 Unauthorized` on `PATCH /zones/{id}/settings/ssl`,
  `always_use_https` and `min_tls_version`. Use a user token for these settings.
- **Hetzner**: Cloud Console → project `disclosly` → Security → API Tokens, Read & Write.

```bash
export HCLOUD_TOKEN='...'          # laptop
export CLOUDFLARE_API_TOKEN='...'  # laptop
```

Check the Cloudflare token without printing it:

```bash
curl -s -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" "https://api.cloudflare.com/client/v4/zones?name=disclosly.com" | grep -o '"success":[a-z]*'
```

`Error: invalid header field value for "Authorization"` from Terraform means the exported token
contains a newline or a non-ASCII character. Export it again on one line.

### 3. SSH keys

- Maintainer key: `~/.ssh/id_ed25519.pub`.
- CI deploy key, no passphrase (its private half becomes the `DEPLOY_SSH_KEY` GitHub secret):

  ```bash
  ssh-keygen -t ed25519 -f ~/.ssh/disclosly_deploy -N "" -C github-actions-deploy
  ```

Both public keys are authorized for the `deploy` user through cloud-init. Root login and password
login are disabled.

### 4. Server type

`server_type` defaults to `cx23` (2 shared x86 vCPU, 4 GB RAM, 40 GB disk). Confirm the name still
exists before `apply`:

```bash
curl -s -H "Authorization: Bearer $HCLOUD_TOKEN" https://api.hetzner.cloud/v1/server_types | python3 -c "import sys,json; [print(t['name'], t['cores'], 'vCPU', t['memory'], 'GB RAM', t['disk'], 'GB disk', t['architecture'], 'deprecated' if t['deprecated'] else '') for t in json.load(sys.stdin)['server_types']]"
```

Do not pick a `cax*` type: those are ARM and the API image is built for amd64.

### 5. Apply

```bash
cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars   # laptop, then fill in the keys
terraform -chdir=infra/terraform init
terraform -chdir=infra/terraform plan    # expect 9 resources with one maintainer key
terraform -chdir=infra/terraform apply
terraform -chdir=infra/terraform output -raw server_ipv4
```

Billing starts when `apply` creates the server. The three `cloudflare_zone_setting` resources
cannot be destroyed by Terraform; their values stay on the zone after `destroy`.

If the zone settings cannot be applied, set them by hand in the dashboard so the site works
(SSL/TLS → Overview → **Full (strict)**; Edge Certificates → **Always Use HTTPS** off,
**Minimum TLS Version** 1.2), then fix the token and run `apply` again. With "Flexible" SSL the API
loops between Cloudflare's HTTP request and Caddy's HTTPS redirect.

### 6. Check the server

cloud-init takes 2–3 minutes after `apply`.

```bash
ssh deploy@<server_ipv4>                                                    # laptop
cloud-init status --wait && docker --version && docker compose version && swapon --show   # server
```

Expected: `status: done`, Docker and Compose versions, and a 4 GB `/swapfile`.

Terminals that send their own terminal type (for example Ghostty, `xterm-ghostty`) break `nano`
and other full-screen tools on the server with `Error opening terminal`. Run
`export TERM=xterm-256color` in the session, or install the terminfo once:

```bash
infocmp -x xterm-ghostty | ssh deploy@<server_ipv4> -- tic -x -   # laptop
```

## First deploy

`deploy.sh` only replaces the `api` container (`up -d --no-deps api`), so the first start brings
up the whole stack by hand. Later deploys use `deploy.sh` (CI does this from Phase 5 on).

### 1. Server `.env`

The Postgres password is generated on the server and never shown.

```bash
cd ~/disclosly && umask 077 && PGPASS="$(openssl rand -hex 24)" && cat > .env <<EOF
APP_ENV=production
DATABASE_URL=postgresql://disclosly:${PGPASS}@postgres:5432/disclosly
REDIS_URL=redis://redis:6379/0
CORS_ALLOWED_ORIGINS=https://disclosly.com,https://www.disclosly.com
LOG_LEVEL=INFO

POSTGRES_USER=disclosly
POSTGRES_PASSWORD=${PGPASS}
POSTGRES_DB=disclosly

API_IMAGE_TAG=none
API_DOMAIN=api.disclosly.com
ACME_EMAIL=<maintainer email>
EOF
unset PGPASS; chmod 600 .env
```

The variables are described in `specs/001-walking-skeleton/contracts/configuration.md`.

### 2. First image

Build from a commit that is on `main` (or about to be). The tag is the full commit SHA, and the
image reports it as `version` on `/healthz`.

```bash
SHA=$(git rev-parse HEAD)                                                              # laptop
docker build -f api/Dockerfile --build-arg APP_VERSION=$SHA -t ghcr.io/omar9564/disclosly-api:$SHA .
docker login ghcr.io -u OMAR9564        # password: classic PAT with write:packages, short expiry
docker push ghcr.io/omar9564/disclosly-api:$SHA
```

Then make the package public (GitHub → Packages → `disclosly-api` → Package settings → Change
visibility), so the server pulls without credentials. The image holds no secrets: `.dockerignore`
allows only the Python project files and `api/`. Delete the PAT afterwards.

### 3. Copy files and start

```bash
scp infra/docker-compose.yml infra/Caddyfile infra/scripts/deploy.sh \
  deploy@$(terraform -chdir=infra/terraform output -raw server_ipv4):~/disclosly/      # laptop
```

```bash
cd ~/disclosly && chmod +x deploy.sh                                                  # server
sed -i "s/^API_IMAGE_TAG=.*/API_IMAGE_TAG=<sha>/" .env
alias dc='docker compose --env-file .env -f docker-compose.yml'
dc pull && dc --profile migrate run --rm migrate && dc up -d && dc ps
dc logs -f caddy      # wait for "certificate obtained successfully"
```

`dc ps` lists `caddy`, `api`, `postgres` and `redis` as running, and the last two as healthy.

### 4. Validate (quickstart V4, V6)

```bash
curl -s https://api.disclosly.com/healthz          # {"status":"ok","version":"<sha>",...}
curl -s https://api.disclosly.com/readyz           # "status":"ready"
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://api.disclosly.com/healthz   # 308 to https
nc -z -w 5 <server_ipv4> 5432; nc -z -w 5 <server_ipv4> 6379   # both fail
ssh -o PubkeyAuthentication=no root@<server_ipv4>              # Permission denied (publickey)
```

Repeat the HTTPS check from a second network (for example a phone hotspot). Finally run the deploy
script once with the same SHA; it must end with `deploy: live: <sha>`:

```bash
./deploy.sh <sha>    # server
```

## Repository settings

Done once by the owner in the GitHub repository settings. CI cannot enforce these itself, so check
them again after any change to the required checks in `.github/workflows/ci.yml` or
`.github/workflows/codeql.yml`.

### 1. Merge options

Settings → General → Pull Requests: allow **squash merging** only. Turn off merge commits and
rebase merging. Turn on "Automatically delete head branches".

```bash
gh api -X PATCH repos/OMAR9564/disclosly -F allow_squash_merge=true -F allow_merge_commit=false -F allow_rebase_merge=false -F delete_branch_on_merge=true   # laptop
```

### 2. Branch ruleset on `main`

Settings → Rules → Rulesets → New branch ruleset:

- Name `main`, enforcement **Active**, target the default branch, **no bypass actors**.
- Restrict deletions; block force pushes.
- Require a pull request before merging, with allowed merge method **Squash** only.
- Require status checks to pass, with the branch up to date: `lint`, `typecheck`,
  `test-backend`, `test-web`, `evals`, `build`, `security` and `CodeQL`.

The same ruleset from the command line:

```bash
gh api -X POST repos/OMAR9564/disclosly/rulesets --input - <<'JSON'   # laptop
{
  "name": "main",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [],
  "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "pull_request", "parameters": {
      "required_approving_review_count": 0,
      "dismiss_stale_reviews_on_push": false,
      "require_code_owner_review": false,
      "require_last_push_approval": false,
      "required_review_thread_resolution": false,
      "allowed_merge_methods": ["squash"]
    }},
    {"type": "required_status_checks", "parameters": {
      "strict_required_status_checks_policy": true,
      "required_status_checks": [
        {"context": "lint"}, {"context": "typecheck"}, {"context": "test-backend"},
        {"context": "test-web"}, {"context": "evals"}, {"context": "build"},
        {"context": "security"}, {"context": "CodeQL"}
      ]
    }}
  ]
}
JSON
```

A check name only appears in the picker after it has run once, so open the Phase 4 pull request
first and create the ruleset while it is open. `CodeQL` is the summary check that code scanning
posts once both language analyses have uploaded, not the `CodeQL (python)` or
`CodeQL (javascript-typescript)` job names.

### 3. Security features

Settings → Advanced Security (or Code security):

- Secret scanning: on, with **push protection** on.
- Dependabot alerts: on. Dependabot version updates come from `.github/dependabot.yml`.
- Private vulnerability reporting: on (`SECURITY.md` points reporters to it).
- Code scanning: leave default setup **off**. The advanced setup in `codeql.yml` runs instead,
  and GitHub rejects uploads from both at once.

```bash
gh api -X PATCH repos/OMAR9564/disclosly --input - <<'JSON'   # laptop
{"security_and_analysis": {
  "secret_scanning": {"status": "enabled"},
  "secret_scanning_push_protection": {"status": "enabled"}
}}
JSON
gh api -X PUT repos/OMAR9564/disclosly/vulnerability-alerts            # laptop
gh api -X PUT repos/OMAR9564/disclosly/private-vulnerability-reporting # laptop
```

### 4. Validate (quickstart V5 steps 3–4)

Open one throwaway pull request per case below. Each must turn its check red and show the merge
button blocked. Close each one without merging and delete its branch.

| Change | Red check |
|---|---|
| `import os` unused in an `api/` module | `lint` |
| `x: int = "a"` in an `api/` module | `typecheck` |
| `assert False` in a unit test | `test-backend` |
| a new `api/` module with untested branches, enough to drop total coverage below 80% | `test-backend` |
| a gitleaks test pattern (for example a fake `ghp_` token) in a new file | `security` |

Then try a direct push. GitHub must reject it with a ruleset violation:

```bash
git push origin HEAD:main   # laptop
```

## Deploy secrets

Terraform must be installed on the laptop, and `HCLOUD_TOKEN` and `CLOUDFLARE_API_TOKEN` must be
exported in the same shell (see Provisioning). Run it as `terraform -chdir=infra/terraform …`.

`deploy.yml` reaches the server as `deploy` with a key that is used for nothing else.

```bash
ssh-keygen -t ed25519 -N '' -C 'github-actions-deploy' -f /tmp/disclosly-deploy   # laptop
ssh deploy@<server> 'cat >> ~/.ssh/authorized_keys' < /tmp/disclosly-deploy.pub
gh secret set DEPLOY_SSH_KEY < /tmp/disclosly-deploy
gh secret set DEPLOY_HOST --body "$(terraform -chdir=infra/terraform output -raw server_ipv4)"
gh secret set DEPLOY_KNOWN_HOSTS --body "$(ssh-keyscan -t ed25519 <server ip>)"
shred -u /tmp/disclosly-deploy /tmp/disclosly-deploy.pub
```

Check the `ssh-keyscan` fingerprint against the server console (`ssh-keygen -lf
/etc/ssh/ssh_host_ed25519_key.pub`) before saving it. The workflow never disables host key checks.

## Vercel

1. Create a Vercel project from this repository with **Root Directory** `web/`. Git deploys stay
   off: `web/vercel.json` sets `git.deploymentEnabled: false`, so only `deploy.yml` publishes.
2. Domains: add `disclosly.com` (primary) and `www.disclosly.com` (redirect to apex). Put the DNS
   targets Vercel shows into the Terraform variables and run `terraform apply`. The defaults
   (`76.76.21.21`, `cname.vercel-dns.com`) are Vercel's legacy targets and still work.
   Check that the apex has no other records: when the zone was created, Cloudflare imported two
   proxied A records from the registrar's parking page, and they caused HTTP 522 until deleted.
   Manage DNS only through Terraform.
3. Create a token (Account Settings → Tokens, scoped to the team) and read the IDs from
   `.vercel/project.json` after `npx vercel link` in the repository root:

```bash
gh secret set VERCEL_TOKEN                                   # laptop, paste when prompted
gh secret set VERCEL_ORG_ID --body "<orgId>"
gh secret set VERCEL_PROJECT_ID --body "<projectId>"
```

## Deploy

Every squash merge to `main` runs `CI`. When `CI` passes, `Deploy` (`.github/workflows/deploy.yml`)
starts two independent jobs for the merge commit SHA:

- `api`: builds and pushes `ghcr.io/omar9564/disclosly-api:<sha>` and `:main`, copies
  `docker-compose.yml`, `Caddyfile` and `deploy.sh` to `~/disclosly/`, runs `deploy.sh <sha>`,
  then checks that `/healthz` reports `<sha>`.
- `web`: `vercel pull`, `vercel build --prod`, `vercel deploy --prebuilt --prod`, with
  `NEXT_PUBLIC_APP_VERSION=<sha>`.

Deploys run one at a time (`concurrency: deploy-production`). To redeploy a commit, re-run the
`Deploy` workflow run for it in the Actions tab.

Validate (quickstart V5 steps 1–2): merge a trivial PR and confirm within 15 minutes that both
`/healthz` and the web status show the new SHA.

## Rollback

`deploy.sh` rolls back on its own: if `/healthz` does not report the new SHA within 60 s, it
restores the previous `API_IMAGE_TAG`, restarts `api` and exits 2, which fails the `api` job.
A failed migration or image pull exits 1 before the switch, so the old container keeps running.

Manual rollback to any earlier image:

```bash
ssh deploy@<server> 'bash ~/disclosly/deploy.sh <older sha>'   # laptop
```

Migrations are not reverted by either path; write them backward compatible. For the web app,
promote an earlier deployment in the Vercel dashboard (Deployments → … → Promote to Production).

Validate (quickstart V7): merge a PR that makes `/healthz` return 500. The `api` job must fail,
`/healthz` must still report the previous SHA, with less than 1 minute of failed checks. Then
revert the change through a PR.

Drill result (2026-10-18, PR #20): `deploy.sh` rolled back to the previous SHA as designed, but
the failed checks lasted about 64 s (2 × 502 while the container switched, ~28 × 500, 2 × 502
while it switched back). This is slightly above the 1-minute target of SC-006 and was accepted.
The cause is that `deploy.sh` waits the full 60 s even when `/healthz` returns 5xx. Rolling back
after a few consecutive 5xx responses would cut this to about 15 s if the target becomes strict.

## Logs

The commands below run on the server as `deploy`.

```bash
cd ~/disclosly && alias dc='docker compose --env-file .env -f docker-compose.yml'   # server
dc ps                         # state and health of every service
dc logs --tail 200 api        # last 200 API log lines (JSON, one event per line)
dc logs -f api                # follow the API logs
dc logs --since 1h caddy      # proxy and certificate events of the last hour
```

API logs are JSON. Every request line carries `request_id`, which matches the `X-Request-ID`
response header, so a user-reported failure can be traced with:

```bash
dc logs api | grep '"request_id": "<id>"'   # server
```

## Server reboot

Every service has `restart: unless-stopped`, and Docker starts at boot, so a reboot needs no
manual step.

The `deploy` user has no password, so `sudo reboot` over SSH does not work. Reboot from Hetzner
instead: Cloud Console → project `disclosly` → the server → Power → **Shutdown** (a clean ACPI
shutdown), wait for the server to show as off, then **Power on**. Do not use Power off or Power
cycle: both cut power like pulling the plug, and Postgres can lose data. The `hcloud` CLI does
the same clean reboot in one step (`hcloud server reboot <name>`, with `HCLOUD_TOKEN` exported).
Then:

```bash
until curl -fsS https://api.disclosly.com/healthz; do sleep 5; done # laptop, < 5 min
ssh deploy@<server> 'uptime -p'                                     # must show minutes
ssh deploy@<server> 'cd ~/disclosly && docker compose --env-file .env -f docker-compose.yml ps'
```

All four services must be running, and `postgres` and `redis` must be healthy. To check memory:

```bash
ssh deploy@<server> 'docker stats --no-stream'   # laptop
```

## Certificate renewal check

Caddy renews the Let's Encrypt certificate on its own, about 30 days before it expires. It keeps
the certificate in the `caddy_data` volume, so a container restart does not request a new one.
Check the expiry date of the certificate Caddy serves:

```bash
ssh deploy@<server> "echo | openssl s_client -connect localhost:443 -servername api.disclosly.com 2>/dev/null | openssl x509 -noout -enddate"
```

The public address shows Cloudflare's edge certificate instead. Fewer than 20 days left on the
origin certificate means renewal is failing. Read why:

```bash
ssh deploy@<server> 'cd ~/disclosly && docker compose --env-file .env -f docker-compose.yml logs --since 72h caddy | grep -i -E "renew|acme|error"'
```

The usual cause is the HTTP-01 challenge not reaching Caddy: check that "Always Use HTTPS" is
still off in Cloudflare and that port 80 is open in the Hetzner firewall.

## Full server rebuild

Use this when the server is lost or broken beyond repair. The database is rebuilt empty, so
restore data from a backup if one exists (Phase 1 stores no user data).

1. Make sure the Terraform state is current (see "Terraform state backup").
2. Replace the server and let cloud-init prepare the new one:

   ```bash
   terraform -chdir=infra/terraform apply -replace=hcloud_server.api   # laptop
   ```

   The Cloudflare `api` record follows the new IPv4 address in the same apply.
3. Update the host secrets for the deploy workflow, because the IP address and the host key
   changed:

   ```bash
   IP=$(terraform -chdir=infra/terraform output -raw server_ipv4)                   # laptop
   gh secret set DEPLOY_HOST --body "$IP"
   gh secret set DEPLOY_KNOWN_HOSTS --body "$(ssh-keyscan -t ed25519 "$IP" 2>/dev/null)"
   ```

   Check the fingerprint against the server console, as in "Deploy secrets".
4. Add the deploy public key to `~deploy/.ssh/authorized_keys` on the new server.
5. Follow "First deploy" steps 1 and 3. Use the SHA that is live on `main` as `API_IMAGE_TAG`;
   its image is already in GHCR.
6. Validate with `curl -s https://api.disclosly.com/healthz` and by re-running the latest
   `Deploy` workflow run.

## Secret rotation

Rotate a secret right away if it may have leaked, and otherwise once a year.

### Deploy key

```bash
ssh-keygen -t ed25519 -N '' -C 'github-actions-deploy' -f /tmp/disclosly-deploy   # laptop
ssh deploy@<server> 'cat >> ~/.ssh/authorized_keys' < /tmp/disclosly-deploy.pub
gh secret set DEPLOY_SSH_KEY < /tmp/disclosly-deploy
shred -u /tmp/disclosly-deploy /tmp/disclosly-deploy.pub
```

Re-run the latest `Deploy` run to prove the new key works. Then remove the old public key line
from `~deploy/.ssh/authorized_keys` on the server.

### Vercel token

1. Vercel → Account Settings → Tokens: create a new token with the same scope.
2. Save it:

   ```bash
   gh secret set VERCEL_TOKEN   # laptop, paste when prompted
   ```

3. Re-run the latest `Deploy` run and check that the `web` job is green.
4. Delete the old token in Vercel.

### Postgres password

The password appears twice in the server `.env`: in `POSTGRES_PASSWORD` and inside
`DATABASE_URL`. Changing `POSTGRES_PASSWORD` alone has no effect on an existing database, so
change it in Postgres first.

```bash
cd ~/disclosly && alias dc='docker compose --env-file .env -f docker-compose.yml'   # server
NEW="$(openssl rand -hex 24)"
dc exec -T postgres psql -U disclosly -d disclosly -c "ALTER USER disclosly WITH PASSWORD '$NEW';"
cp .env .env.bak && chmod 600 .env.bak
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$NEW|" .env
sed -i "s|^DATABASE_URL=postgresql://disclosly:[^@]*@|DATABASE_URL=postgresql://disclosly:$NEW@|" .env
unset NEW
dc up -d --no-deps api
curl -fsS https://api.disclosly.com/readyz && shred -u .env.bak
```

If `/readyz` does not report `ready`, restore `.env.bak` and run `dc up -d --no-deps api` again.

### Provider tokens (Hetzner, Cloudflare)

These are never stored anywhere. Create a new token in the provider console, delete the old one,
and export the new one in the shell that runs Terraform (see "Provisioning").

## Terraform state backup

The state is the local file `infra/terraform/terraform.tfstate`. It is gitignored and holds
resource IDs and the server IP, but no provider tokens. Back it up after every `apply`, encrypted,
outside the repository:

```bash
gpg --symmetric --cipher-algo AES256 \
  -o ~/Backups/disclosly-tfstate-$(date +%F).gpg infra/terraform/terraform.tfstate   # laptop
```

Keep a copy outside the laptop too (for example, a password manager attachment). To restore:

```bash
gpg -d ~/Backups/disclosly-tfstate-<date>.gpg > infra/terraform/terraform.tfstate   # laptop
terraform -chdir=infra/terraform plan   # must show no changes
```

Without any backup, run `terraform import` for each resource listed in `infra/terraform/main.tf`
before the next `apply`.
