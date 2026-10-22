# Contract: Agent graph (Phase 4)

Graph shape and node responsibilities: research §R2. State: [data-model.md](../data-model.md).
This file fixes what each node may do, so tool access and budgets can be tested per node.

## Nodes

| Node | Model calls | Tools / side effects allowed | Output to state |
|---|---|---|---|
| `router` | 1 `cheap` (`RouterDecision` schema) | read memory (code) | `language`, `mode`, `router`, `memory`, `deadline_at` (simple) |
| `memory` | 0 | upsert/delete one watchlist item from `router.memory_intent` | event `memory_updated` |
| `quota_gate` | 0 | read deep quota | route to `planner` or `decision[deep_quota_offer]` |
| `planner` | 1 `strong` (`Plan` schema) | none | `plan` (validated in code; revisions limited to the approved scope) |
| `decision` | 0 | `interrupt()`; updates run row to `awaiting_approval` before pausing | `plan_approved`, `plan`, `deadline_at` (deep) |
| `retriever` | 1–3 `cheap` (sufficiency + up to 2 rewrites) | `search_documents`, `get_financial_facts`, `list_documents` | `evidence`, step statuses |
| `retriever_simple` | ≤ 2 `cheap` with tools | same three tools | `evidence` |
| `analyst` | 1 `cheap` or `strong` (`AnalystOutput` schema) + 1 re-ask | `run_python` (sandbox) | `findings`, `calculations` |
| `verifier` | 1 `strong` (claim support) | read facts/chunks by id, `run_python` | `verification`, `verify_rounds` |
| `writer` | 1 `strong` (`Report` schema) + 1 re-ask | none | `draft` |
| `writer_simple` | 1 `strong` (`Answer`/`Report`) + 1 re-ask | none | `draft` |
| `finalize_partial` | 0 | none | partial `draft` from verified material |

The analyst's task class is `strong` when the plan has a `calculate` step, otherwise `cheap`.
A node may not call a tool outside its row; the registry is built per node and a test checks
each node's tool list.

## Budgets (defaults, settings)

| Mode | `max_model_calls` | Deadline | Other |
|---|---|---|---|
| simple | 4 (`AGENT_MAX_MODEL_CALLS`) | 90 s from start (`AGENT_DEADLINE_SECONDS`) | 6 tool calls (`AGENT_MAX_TOOL_CALLS`) |
| deep | 20 (`AGENT_DEEP_MAX_MODEL_CALLS`) | 300 s from approval (`AGENT_DEEP_DEADLINE_SECONDS`) | 12 node steps after approval (`AGENT_DEEP_MAX_STEPS`), 24 tool calls, 4 sandbox runs |

Other settings: `AGENT_ENGINE` (`graph` \| `single`), `AGENT_PLAN_MAX_STEPS=8`,
`AGENT_PLAN_TTL_HOURS=24`, `AGENT_VERIFY_MAX_ROUNDS=2`, `AGENT_MAX_CONCURRENT_RUNS=3` (per
API worker), `QUOTA_DAILY_DEEP=5`, `SANDBOX_URL=http://sandbox:8100`,
`SANDBOX_IMAGE_TAG`, `CHECKPOINT_RETENTION_DAYS=7`.

## Untrusted data

Unchanged from Phase 3 for tool results (`<untrusted_document cite_id="…">`). New:
memory values reach the planner inside `<user_memory>` with the same escaping; verifier
excerpts inside `<untrusted_document>`; calculation code and outputs are never shown to the
router or planner. System prompts for every node state that tagged content is data.

## Prompts and schema versions

Prompts live in `agent/prompts/` per node and language. Graph nodes send tool schema version
`"2"` (tool set per node); the `single` engine keeps `"1"`, so its Phase 3 cassettes and cache
entries stay valid for the comparison.
Each node's response schema is part of the cache key (unchanged rule).
