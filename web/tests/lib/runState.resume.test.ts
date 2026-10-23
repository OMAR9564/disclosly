import { describe, expect, it } from "vitest";

import { initialRunState, reduceRun, type RunState } from "@/lib/runState";
import { reconnectDelay, type RunEvent } from "@/lib/stream";

/** Events of a graph run stopped by a restart inside `retriever_simple` and resumed. */
function events(list: [string, unknown][]): RunEvent[] {
  return list.map(
    ([type, data], i) => ({ type, id: `${i + 1}-0`, data }) as RunEvent,
  );
}

const at = (node: string | null, attempt = 1) => ({ node, attempt });

const BEFORE_RESTART = events([
  ["run_started", { run_id: "r", language: "tr", ...at(null) }],
  ["model_called", { task_class: "cheap", provider: "groq", ...at("router") }],
  ["mode_selected", { mode: "simple", uses_watchlist: false, ...at("router") }],
  [
    "tool_called",
    { tool: "search_documents", summary: "THYAO", ...at("retriever_simple") },
  ],
  [
    "tool_result",
    { tool: "search_documents", count: 4, ...at("retriever_simple") },
  ],
]);

const AFTER_RESTART: RunEvent[] = [
  [
    "run_resumed",
    { from_node: "retriever_simple", resume_count: 1, ...at(null) },
  ],
  [
    "tool_called",
    {
      tool: "search_documents",
      summary: "THYAO",
      ...at("retriever_simple", 2),
    },
  ],
  [
    "tool_result",
    { tool: "search_documents", count: 5, ...at("retriever_simple", 2) },
  ],
].map(([type, data], i) => ({ type, id: `${10 + i}-0`, data }) as RunEvent);

const run = (list: RunEvent[], state: RunState = initialRunState) =>
  list.reduce(reduceRun, state);

describe("a run resumed after a restart", () => {
  it("notes the resume and keeps running", () => {
    const state = run([...BEFORE_RESTART, AFTER_RESTART[0]!]);
    expect(state.resumes).toBe(1);
    expect(state.phase).toBe("running");
    expect(state.lastId).toBe("10-0");
  });

  it("replaces the stopped node's earlier attempt instead of showing it twice", () => {
    const state = run([...BEFORE_RESTART, ...AFTER_RESTART]);
    const searches = state.steps.filter((s) => s.tool === "search_documents");
    expect(searches).toHaveLength(1);
    expect(searches[0]).toMatchObject({ attempt: 2, count: 5 });
    expect(state.counts.search).toBe(5);
    // The router finished before the restart: its model call is kept.
    expect(state.counts.model).toBe(1);
  });

  it("starts with no resume", () => {
    expect(run(BEFORE_RESTART).resumes).toBe(0);
  });
});

describe("reconnect waits", () => {
  it("double from 1 s and stop growing at 30 s", () => {
    expect([0, 1, 2, 3, 4, 5, 9].map(reconnectDelay)).toEqual([
      1000, 2000, 4000, 8000, 16000, 30000, 30000,
    ]);
  });
});
