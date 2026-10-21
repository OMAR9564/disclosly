import type { Answer, AnswerStatus, Citation } from "@/lib/api";

/** Typed run events (contracts/sse-events.md) over EventSource with cookies. */

export type ToolName =
  "search_documents" | "get_financial_facts" | "list_documents";

export interface RunEventMap {
  run_started: { run_id: string; language: "tr" | "en" };
  tool_called: { tool: ToolName; summary: string };
  tool_result: { tool: ToolName; count: number };
  model_called: {
    task_class: "cheap" | "strong";
    provider: string;
    cache_hit: boolean;
    fallback_from: string | null;
  };
  answer_delta: { text: string };
  final: { status: AnswerStatus; answer: Answer; citations: Citation[] };
  error: { code: string; message: string };
}

export type RunEventType = keyof RunEventMap;

export type RunEvent = {
  [K in RunEventType]: { type: K; id: string; data: RunEventMap[K] };
}[RunEventType];

export const RUN_EVENT_TYPES: RunEventType[] = [
  "run_started",
  "tool_called",
  "tool_result",
  "model_called",
  "answer_delta",
  "final",
  "error",
];

export const MAX_RECONNECTS = 5;

export interface StreamHandlers {
  onEvent: (event: RunEvent) => void;
  /** The connection failed more than MAX_RECONNECTS times in a row. */
  onDisconnect?: () => void;
}

type EventSourceFactory = (url: string, init: EventSourceInit) => EventSource;

const defaultFactory: EventSourceFactory = (url, init) =>
  new EventSource(url, init);

/**
 * Subscribe to a run. EventSource resends `Last-Event-ID` on its own reconnects, so a dropped
 * connection resumes after the last event; the stream is closed after `final` or `error`.
 * Returns a function that closes the stream.
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
      handlers.onEvent({ type, id: message.lastEventId, data } as RunEvent);
      if (type === "final" || type === "error") close();
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
