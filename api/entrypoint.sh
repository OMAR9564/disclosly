#!/bin/sh
# Validates configuration, clears stale Prometheus multiprocess files from a previous run,
# then runs the command (uvicorn by default; the migrate service passes `alembic upgrade head`).
set -eu

# Exits non-zero and names the bad variable. Done here because uvicorn's worker supervisor
# would otherwise keep respawning workers that fail at startup.
python -m api.config

dir="${PROMETHEUS_MULTIPROC_DIR:-/tmp/prometheus}"
mkdir -p "$dir"
find "$dir" -mindepth 1 -delete

exec "$@"
