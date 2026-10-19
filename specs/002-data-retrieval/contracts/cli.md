# Contract: Commands

All commands exit 0 on success, 1 on failure, and log JSON lines to stderr.

| Command | Purpose | Key options |
|---|---|---|
| `python -m ingestion.run ingest` | Fetch, parse, chunk, extract facts, clean up | `--tickers THYAO,AAPL`, `--source kap\|sec`, `--since 2024-10-01`, `--dry-run` |
| `python -m ingestion.run status` | Counts per status, last run, disk usage | `--json` |
| `python -m ingestion.run rechunk` | Build chunks for another chunker version (sweep) | `--size 400 --overlap 64` |
| `python -m ingestion.embed` | Embed chunks with null embedding | `--batch 64`, `--limit N`, `--chunker-version v1-600-64` |
| `python -m evals.retrieval` | Run golden set, write report | `--configs v1-400-64,v1-600-64 --rerank on,off`, `--cassettes-only` |

`ingest` output summary (stdout, JSON): `{"run_id": 7, "fetched": 12, "skipped": 340,
"failed": 1, "duration_s": 512.4}`.

Environment: `DATABASE_URL`, `REDIS_URL`, `SEC_USER_AGENT` (required for SEC),
`INGEST_DATA_DIR` (raw files, temporary), `CORPUS_DISK_LIMIT_GB` (default 20).

The laptop runs `ingestion.embed` with `DATABASE_URL` pointing at the server through an SSH
tunnel; no new network exposure.

arq jobs (worker): `ingest_company(ticker)`, `embed_pending(limit=500)`; a nightly cron runs
`ingest_company` for every configured ticker.
