#!/usr/bin/env bash
# Deploy one API image to the server, with health-checked rollback (research.md R7). The
# sandbox service runs from the same image and is switched with it (Phase 4 research §R8).
#
# Usage: deploy.sh <git-sha>
#
# Runs as the `deploy` user in DEPLOY_DIR (default ~/disclosly), which holds .env,
# docker-compose.yml and Caddyfile. Exit codes: 0 deployed, 1 failed before the switch
# (old version still running), 2 health check failed and the old version was restored.
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-$HOME/disclosly}"
HEALTH_TIMEOUT_S="${DEPLOY_HEALTH_TIMEOUT_S:-60}"
POLL_INTERVAL_S="${DEPLOY_POLL_INTERVAL_S:-2}"

log() { echo "deploy: $*" >&2; }

if [[ $# -ne 1 ]]; then
  echo "usage: deploy.sh <git-sha>" >&2
  exit 1
fi
new_tag="$1"
if [[ ! "$new_tag" =~ ^[0-9a-f]{7,40}$ ]]; then
  log "invalid sha '$new_tag' (expected 7-40 lowercase hex chars)"
  exit 1
fi

cd "$DEPLOY_DIR"
env_file=".env"
compose=(docker compose --env-file "$env_file" -f docker-compose.yml)

read_env() {
  grep -E "^$1=" "$env_file" | tail -n 1 | cut -d= -f2- || true
}

set_tag() {
  local tmp
  tmp="$(mktemp "$env_file.XXXXXX")"
  grep -v -E '^API_IMAGE_TAG=' "$env_file" > "$tmp" || true
  echo "API_IMAGE_TAG=$1" >> "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$env_file"
}

healthy_version() {
  local body
  body="$(curl -fsS --max-time 5 "https://$api_domain/healthz" 2>/dev/null)" || return 1
  sed -n 's/.*"version":"\([^"]*\)".*/\1/p' <<< "$body"
}

# Old API and worker images fill the disk (a worker image is about 4 GB). Kept: the new and
# the previous API image (for a rollback) and the worker image the .env names. The sandbox
# image is never removed: the sandbox service starts it per batch, so no container holds it.
remove_old_images() {
  local worker_tag image tag
  worker_tag="$(read_env WORKER_IMAGE_TAG)"
  docker images --format '{{.Repository}}:{{.Tag}}' | while read -r image; do
    tag="${image##*:}"
    case "$image" in
      ghcr.io/omar9564/disclosly-api:*)
        [[ "$tag" == "$new_tag" || "$tag" == "$previous_tag" ]] && continue ;;
      ghcr.io/omar9564/disclosly-worker:*)
        [[ "$tag" == "${worker_tag:-main}" ]] && continue ;;
      *) continue ;;
    esac
    docker rmi "$image" > /dev/null 2>&1 || log "warning: could not remove $image"
  done
  docker image prune -f > /dev/null || true
}

api_domain="$(read_env API_DOMAIN)"
if [[ -z "$api_domain" ]]; then
  log "API_DOMAIN is not set in $DEPLOY_DIR/$env_file"
  exit 1
fi

# 1. Remember the running version.
previous_tag="$(read_env API_IMAGE_TAG)"
mkdir -p .deploy
echo "$previous_tag" > .deploy/previous
log "current version: ${previous_tag:-<none>}, deploying: $new_tag"

# 2. Pull the new image.
set_tag "$new_tag"
if ! "${compose[@]}" pull api; then
  set_tag "$previous_tag"
  log "image pull failed; still running $previous_tag"
  exit 1
fi

# 3. Migrate: Alembic, then the graph checkpoint tables (the `migrate` service command).
#    On failure the old container keeps running untouched.
if ! "${compose[@]}" --profile migrate run --rm migrate; then
  set_tag "$previous_tag"
  log "migration failed; still running $previous_tag"
  exit 1
fi

# 4. Pull the calculation image the sandbox service starts per batch (contracts/sandbox.md).
#    A failed pull keeps the image already on the host; calculations report the sandbox as
#    unavailable until an image is there, the rest of the API is unaffected.
sandbox_tag="$(read_env SANDBOX_IMAGE_TAG)"
if ! docker pull "ghcr.io/omar9564/disclosly-sandbox:${sandbox_tag:-main}" > /dev/null; then
  log "warning: sandbox image pull failed; keeping the local one"
fi

# 5. Switch the API and sandbox service containers (same image).
"${compose[@]}" up -d --no-deps api sandbox

# 6. Wait until the public endpoint reports the new version.
deadline=$((SECONDS + ${HEALTH_TIMEOUT_S%.*}))
while ((SECONDS < deadline)); do
  if [[ "$(healthy_version || true)" == "$new_tag" ]]; then
    log "live: $new_tag"
    remove_old_images
    exit 0
  fi
  sleep "$POLL_INTERVAL_S"
done

# 7. Roll back.
log "health check did not report $new_tag within ${HEALTH_TIMEOUT_S}s; rolling back"
set_tag "$previous_tag"
"${compose[@]}" up -d --no-deps api sandbox
log "rolled back to $previous_tag"
exit 2
