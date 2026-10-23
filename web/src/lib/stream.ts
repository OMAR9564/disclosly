import type {
  AnswerStatus,
  Citation,
  DecisionAction,
  DecisionKind,
  Estimate,
  Plan,
  Report,
  StepStatus,
} from "@/lib/api";

/** Typed run events (contracts/sse-events.md, Phase 3 and Phase 4) over EventSource with
 * cookies. Graph runs add `node` and `attempt` to every event; single-engine runs do not. */

export type ToolName =
  "search_documents" | "get_financial_facts" | "list_documents" | "run_python";

export interface EventOrigin {
  node?: string | null;
  attempt?: number;
}

interface RunEventPayloads {
  run_started: { run_id: string; language: "tr" | "en" };
  mode_selected: {
    mode: "simple" | "deep" | "refused";
    uses_watchlist: boolean;
  };
  plan_proposed: { plan: Plan; revision: number };
  awaiting_decision: {
    kind: DecisionKind;
    plan: Plan | null;
    estimate: Estimate | null;
    deep_remaining: number;
    expires_at: string;
  };
  decision_made: { action: DecisionAction };
  step_started: { step_id: string | null; summary: string };
  step_finished: {
    step_id: string | null;
    status: Exclude<StepStatus, "pending">;
  };
  tool_called: { tool: ToolName; summary: string; step_id?: string | null };
  tool_result: { tool: ToolName; count: number; step_id?: string | null };
  model_called: {
    task_class: "cheap" | "strong";
    provider: string;
    cache_hit: boolean;
    fallback_from: string | null;
  };
  calculation: {
    cite_id: string;
    name: string;
    description: string;
    status: "ok" | "error";
  };
  verification: {
    round: number;
    passed: boolean;
    failures: number;
    next: "writer" | "planner" | "finalize_partial";
  };
  memory_updated: {
    action: "watchlist_add" | "watchlist_remove";
    ticker: string;
  };
  run_resumed: { from_node: string; resume_count: number };
  run_cancelled: Record<never, never>;
  run_expired: Record<never, never>;
  answer_delta: { text: string };
  final: { status: AnswerStatus; answer: Report; citations: Citation[] };
  error: { code: string; message: string };
}

export type RunEventMap = {
  [K in keyof RunEventPayloads]: RunEventPayloads[K] & EventOrigin;
};

export type RunEventType = keyof RunEventMap;

export type RunEvent = {
  [K in RunEventType]: { type: K; id: string; data: RunEventMap[K] };
}[RunEventType];

export const RUN_EVENT_TYPES: RunEventType[] = [
  "run_started",
  "mode_selected",
  "plan_proposed",
  "awaiting_decision",
  "decision_made",
  "step_started",
  "step_finished",
  "tool_called",
  "tool_result",
  "model_called",
  "calculation",
  "verification",
  "memory_updated",
  "run_resumed",
  "run_cancelled",
  "run_expired",
  "answer_delta",
  "final",
  "error",
];

/** The server closes the stream after these; a paused run reconnects after its decision. */
export const CLOSING_EVENTS: ReadonlySet<RunEventType> = new Set([
  "final",
  "error",
  "awaiting_decision",
  "run_cancelled",
  "run_expired",
]);

/** Redis stream ids ("<ms>-<seq>") in order; anything unparsable sorts first. */
export function compareEventIds(a: string, b: string): number {
  const parse = (id: string) => {
    const [ms, seq] = id.split("-").map(Number);
    return [Number.isFinite(ms) ? ms! : -1, Number.isFinite(seq) ? seq! : 0];
  };
  const [am, as] = parse(a);
  const [bm, bs] = parse(b);
  return am! - bm! || as! - bs!;
}

export const MAX_RECONNECTS = 5;

/** Wait before the n-th (0-based) new connection after the stream was lost: 1 s doubling to
 * 30 s, so the page follows the run again soon after the API is back from a restart. */
export function reconnectDelay(attempt: number): number {
  return Math.min(30_000, 1000 * 2 ** Math.max(0, attempt));
}

export interface StreamHandlers {
  /** Events up to this id were already handled (a reconnect after a decision replays the
   * stream from its start, since a new EventSource cannot send `Last-Event-ID`). */
  after?: string | null;
  onEvent: (event: RunEvent) => void;
  /** The connection failed more than MAX_RECONNECTS times in a row. */
  onDisconnect?: () => void;
}

type EventSourceFactory = (url: string, init: EventSourceInit) => EventSource;

const defaultFactory: EventSourceFactory = (url, init) =>
  new EventSource(url, init);

/**
 * Subscribe to a run. EventSource resends `Last-Event-ID` on its own reconnects, so a dropped
 * connection resumes after the last event; the stream is closed after a closing event
 * (`final`, `error`, `awaiting_decision`, `run_cancelled`, `run_expired`). Returns a function
 * that closes the stream.
 */
export function subscribeRun(
  url: string,
  handlers: StreamHandlers,
  factory: EventSourceFactory = defaultFactory,
): () => void {
  const source = factory(url, { withCredentials: true });
  let failures = 0;
  let closed = false;

  const close = () => {
    closed = true;
    source.close();
  };

  for (const type of RUN_EVENT_TYPES) {
    source.addEventListener(type, (raw) => {
      const message = raw as MessageEvent<string>;
      failures = 0;
      let data: unknown;
      try {
        data = JSON.parse(message.data);
      } catch {
        return; // a malformed frame is skipped, never shown
      }
      const after = handlers.after;
      if (after && compareEventIds(message.lastEventId, after) <= 0) return;
      handlers.onEvent({ type, id: message.lastEventId, data } as RunEvent);
      if (CLOSING_EVENTS.has(type)) close();
    });
  }

  source.onerror = () => {
    if (closed) return;
    failures += 1;
    if (failures > MAX_RECONNECTS || source.readyState === 2) {
      close();
      handlers.onDisconnect?.();
    }
  };

  return close;
}
