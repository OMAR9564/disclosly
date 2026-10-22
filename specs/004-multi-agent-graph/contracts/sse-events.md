# Contract: Run event stream (Phase 4)

Extends `specs/003-single-agent-mvp/contracts/sse-events.md`. Framing, `: ping` every 15 s,
`Last-Event-ID` resume and the 24 h stream TTL are unchanged.

Every event's `data` now also has:

- `node`: the graph node that emitted it (`router`, `quota_gate`, `planner`, `decision`,
  `retriever`, `retriever_simple`, `analyst`, `verifier`, `writer`, `writer_simple`,
  `memory`, `finalize_partial`, `supervisor`), or `null` for `run_started`.
- `attempt`: 1 for a node's first execution, +1 each time the node re-runs after a resume.
  Clients drop earlier events of a `(node, step_id)` when an event with a higher `attempt`
  arrives.

The stream closes after `final`, `error`, `awaiting_decision`, `run_cancelled` or
`run_expired`. After posting a decision, the client reconnects with `Last-Event-ID`.

## Events

| Event | Data (besides `node`, `attempt`) |
|---|---|
| `run_started` | `{"run_id", "language"}` (unchanged) |
| `mode_selected` | `{"mode": "simple" \| "deep" \| "refused", "uses_watchlist": bool}` |
| `plan_proposed` | `{"plan": Plan, "revision": int}` — also sent after a verifier revision |
| `awaiting_decision` | `{"kind": "plan" \| "deep_quota_offer", "plan": Plan \| null, "estimate": {"typical", "max"} \| null, "deep_remaining": int, "expires_at": ISO-8601}` |
| `decision_made` | `{"action": "approve" \| "edit" \| "cancel" \| "simple"}` |
| `step_started` | `{"step_id": str \| null, "summary": str}` — user-facing, never raw tool args |
| `step_finished` | `{"step_id", "status": "done" \| "not_covered" \| "failed"}` |
| `tool_called` / `tool_result` | unchanged, plus `step_id` |
| `model_called` | unchanged |
| `calculation` | `{"cite_id": "k:…", "name", "description", "status": "ok" \| "error"}` |
| `verification` | `{"round": int, "passed": bool, "failures": int, "next": "writer" \| "planner" \| "finalize_partial"}` |
| `memory_updated` | `{"action": "watchlist_add" \| "watchlist_remove", "ticker"}` |
| `run_resumed` | `{"from_node": str, "resume_count": int}` |
| `run_cancelled` | `{}` |
| `run_expired` | `{}` |
| `final` | `{"status", "answer": Report, "citations": Citation[]}` — `Report` per [report.md](report.md) |
| `error` | `{"code", "message"}` (unchanged; new run-level codes `sandbox_unavailable`, `interrupted_limit`) |

Payloads never contain memory values other than the ticker in `memory_updated`, emails, keys,
calculation code or raw document text beyond what `final` citations already show.

## Replay after the stream expires

Unchanged: the stored row is replayed as one `final` or `error`. Runs that ended `cancelled`
or `expired` replay as `run_cancelled` / `run_expired`; a run still `awaiting_approval` after
24 h is already `expired` by the sweep.
