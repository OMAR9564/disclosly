# Contract: Calculation sandbox (Phase 4)

Two parts (research §R8): the `sandbox` service (holds the Docker socket, never runs user
code) and the sandbox image (runs one calculation batch per container).

## Service: `POST http://sandbox:8100/run`

Reachable only on the internal compose network `sandbox_net` (`internal: true`), shared with
`api`. No auth on the request (network isolation is the boundary); no port is published.

Request:

```json
{
  "code": "result = {'ratio': inputs['net_debt'] / inputs['ebitda']}",
  "inputs": {"net_debt": "41200000000", "ebitda": "21684210526"},
  "timeout_s": 20
}
```

- `code` ≤ 16 KiB (several calculations of one round concatenated with their own names);
  `inputs` values are decimal strings; the whole payload ≤ 64 KiB after encoding.
- `timeout_s` 1–20; values above 20 are clamped.

Response 200:

```json
{
  "ok": true,
  "outputs": {"ratio": "1.9"},
  "stdout": "",
  "error": null,
  "duration_ms": 812,
  "timed_out": false,
  "oom": false
}
```

- `outputs`: `result` must be a dict of name → int/float/Decimal (or a pandas/numpy scalar),
  serialised as decimal strings; NaN or infinity is an error. Up to 100 outputs.
- `ok = false` with `error` (≤ 2 KiB, last traceback line only) on exceptions, invalid
  `result`, timeout (`timed_out = true`) or OOM kill (`oom = true`).
- `stdout` is truncated to 4 KiB; output beyond 64 KiB total is cut and the run fails.

Other responses: 422 for a bad request, 503 `sandbox_unavailable` when Docker or the image is
not available. The API maps 503 and connection errors to `SandboxUnavailable` (retried once by
the node retry policy, research §R3).

Concurrency: 2 executions at once; later requests wait (no queue limit beyond the caller's
deadline; the API client sets its HTTP timeout to the run's remaining time).

## Container spec (fixed in code, not taken from the request)

| Setting | Value |
|---|---|
| Image | `ghcr.io/omar9564/disclosly-sandbox:<SANDBOX_IMAGE_TAG>` (pulled at deploy) |
| Command | `python -I /runner/run.py` |
| Env | `SANDBOX_PAYLOAD=<base64 JSON>` only |
| `NetworkMode` | `none` |
| `ReadonlyRootfs` | `true` |
| `Memory` / `MemorySwap` | 512 MiB / 512 MiB |
| `NanoCpus` | 1,000,000,000 |
| `PidsLimit` | 64 |
| `CapDrop` | `ALL` |
| `SecurityOpt` | `no-new-privileges` |
| `User` | `65534:65534` |
| `Tmpfs` | `/tmp: rw,noexec,nosuid,size=16m` |
| Mounts, devices, ports | none |
| Lifetime | created per request, killed at timeout, always removed |

## Image

`sandbox/Dockerfile`: `python:3.12-slim`, pinned `pandas` 3.0.x and `numpy` 2.5.x installed
with hashes, `/runner/run.py` owned by root and read-only. No other packages, no shell tools
beyond the base image, `PYTHONDONTWRITEBYTECODE=1`. Built and pushed by CI when `sandbox/`
changes; the deploy pulls it before restarting `sandbox`.

## Escape test cases (tests/integration/sandbox/, marker `sandbox`)

Each must end with `ok = false` (or no effect) within the limits: open a TCP socket to
`1.1.1.1:53` and to `api:8000`; resolve a DNS name; read `/etc/shadow`, `/proc/1/environ`,
`/var/run/docker.sock`; write to `/runner` and `/`; spawn 200 processes; allocate 1 GiB; loop
forever; print 10 MiB; read any environment variable other than `SANDBOX_PAYLOAD`; import a
module not in the image. A benign pandas calculation must still succeed in under 3 s.
