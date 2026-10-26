# Disclosly

Disclosly is a multi-agent research platform that answers questions about company disclosures
(KAP and SEC EDGAR) with cited sources, on a $0 LLM budget.

- Web: <https://disclosly.com>
- API health: <https://api.disclosly.com/healthz>

<!-- Demo video: added in a later phase (PROJECT_SPEC.md §19.1). -->

## Architecture

Runtime, deploy flow, data sources, ingestion pipeline and retrieval flow:
[docs/architecture.md](docs/architecture.md).

<!-- Full agent architecture diagram: added in a later phase (PROJECT_SPEC.md §19.2). -->

## Eval results

Retrieval, measured 2026-10-20 on the full corpus (40 companies, about 2,460 documents) with
68 golden questions (34 Turkish, 34 English). Configuration `v1-600-64`, hybrid search, reranker
on (64 tokens, int8 weights).
Details and the choices behind them: [ADR 0002](docs/adr/0002-retrieval-configuration.md).

| Language | recall@5 | recall@10 | MRR |
|---|---|---|---|
| all | 0.706 | 0.779 | 0.557 |
| Turkish | 0.706 | 0.824 | 0.542 |
| English | 0.706 | 0.735 | 0.573 |

Rerun on the full corpus (needs `uv sync --group ml` and `DATABASE_URL`):

```bash
uv run python -m evals.retrieval --rerank on,off
```

CI runs the cassette subset on every pull request and fails when recall@10 drops more than 0.02
below `evals/baselines/retrieval.json`:

```bash
uv run python -m evals.retrieval --cassettes-only
```

Answers, single agent against the multi-agent graph, measured 2026-10-26 on the same 87 cases
(replayed recordings). Method, losses and how to rerun:
[docs/evals/single-vs-multi.md](docs/evals/single-vs-multi.md).

| Engine | Date | Numeric match (simple) | Numeric match (deep) | Citation coverage | Verified derived numbers | Model calls / run |
|---|---|---|---|---|---|---|
| single | 2026-10-26 | 0.625 | 0.476 | 1.0 | 0.0 | 2.6 |
| graph | 2026-10-26 | 0.625 | 0.805 | 1.0 | 1.0 | 4.32 |

## Cost and latency

Production idle memory (Phase 1, measured 2026-10-19 with `docker stats --no-stream`):

| Service | Used | Limit |
|---|---|---|
| api | 133.7 MiB | 512 MiB |
| postgres | 31.1 MiB | 1 GiB |
| caddy | 12.8 MiB | 64 MiB |
| redis | 3.2 MiB | 160 MiB |
| total | about 181 MiB | 2 GB target |

Search latency on the server (2026-10-20, 2 vCPU, 204 searches, no cache): p50 0.61 s,
p95 0.88 s (target p95 < 1 s). One search process with both models loaded uses about 1.4 GiB.
Worker memory: 38 MiB idle of a 768 MiB limit. Details: runbook, "Search latency".

<!-- Model calls per run, cache hit rate, p95, $0 budget design: later phase (§19.4). -->

## Failure modes and mitigations

<!-- Later phase (PROJECT_SPEC.md §19.5). -->

## Key decisions

- [ADR 0001: walking-skeleton delivery path](docs/adr/0001-walking-skeleton-delivery.md)
- [ADR 0002: retrieval configuration](docs/adr/0002-retrieval-configuration.md)

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
