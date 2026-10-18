# Disclosly

Disclosly is a multi-agent research platform that answers questions about company disclosures
(KAP and SEC EDGAR) with cited sources, on a $0 LLM budget.

- Web: <https://disclosly.com>
- API health: <https://api.disclosly.com/healthz>

<!-- Demo video: added in a later phase (PROJECT_SPEC.md §19.1). -->

## Architecture

Phase 1 runtime and deploy flow: [docs/architecture.md](docs/architecture.md).

<!-- Full agent architecture diagram: added in a later phase (PROJECT_SPEC.md §19.2). -->

## Eval results

<!-- Results table and how to rerun: added in a later phase (PROJECT_SPEC.md §19.3). -->

## Cost and latency

Production idle memory (Phase 1, measured 2026-10-19 with `docker stats --no-stream`):

| Service | Used | Limit |
|---|---|---|
| api | 133.7 MiB | 512 MiB |
| postgres | 31.1 MiB | 1 GiB |
| caddy | 12.8 MiB | 64 MiB |
| redis | 3.2 MiB | 160 MiB |
| total | about 181 MiB | 2 GB target |

<!-- Model calls per run, cache hit rate, p95, $0 budget design: later phase (§19.4). -->

## Failure modes and mitigations

<!-- Later phase (PROJECT_SPEC.md §19.5). -->

## Key decisions

- [ADR 0001: walking-skeleton delivery path](docs/adr/0001-walking-skeleton-delivery.md)

<!-- Agent design ADRs (LangGraph, pgvector, router, sandbox): later phases (§19.6). -->

## Security model

<!-- Later phase (PROJECT_SPEC.md §19.7). -->

## Usage numbers

<!-- Later phase (PROJECT_SPEC.md §19.8). -->

## Local setup

### Prerequisites

- Docker with the Compose plugin
- [uv](https://docs.astral.sh/uv/)
- Node.js 24 (see `web/.nvmrc`)
- make

### Run the stack

```bash
cp .env.example .env
make up
```

`make up` builds the API image, starts the API, Postgres and Redis, and waits until
`http://localhost:8000/readyz` returns 200. The placeholder values in `.env.example` work for local
use.

Start the web app in a second terminal. It runs on <http://localhost:3000> and calls the local API.

```bash
make web
```

Stop the stack:

```bash
make down
```

`make down` keeps the database volume. `make down-v` removes it too.

### Quality gates

The same checks run in CI on every pull request. The integration tests use the Postgres and Redis
from `make up`, so start the stack first.

```bash
uv sync && npm --prefix web ci
make lint typecheck test
```

## Documentation

- [docs/runbook.md](docs/runbook.md): provisioning, deploy, rollback and repository settings.
- [specs/](specs/): feature specifications, plans and tasks (Spec Kit).
- [PROJECT_SPEC.md](PROJECT_SPEC.md): full product and technical specification.
