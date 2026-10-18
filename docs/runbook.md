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
