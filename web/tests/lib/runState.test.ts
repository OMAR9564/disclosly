import { describe, expect, it } from "vitest";

import { initialRunState, reduceRun, type RunState } from "@/lib/runState";
import type { RunEvent } from "@/lib/stream";

import {
  AFTER_DECISION,
  BEFORE_DECISION,
  PLAN,
  REPORT,
} from "../e2e/fixtures/deep";

function events(list: [string, unknown][], from = 1): RunEvent[] {
  return list.map(
    ([type, data], i) => ({ type, id: `${from + i}-0`, data }) as RunEvent,
  );
}

const run = (list: RunEvent[], state: RunState = initialRunState) =>
  list.reduce(reduceRun, state);

describe("deep run events", () => {
  it("pauses on awaiting_decision with the plan, estimate and deep runs left", () => {
    const state = run(events(BEFORE_DECISION));
    expect(state.phase).toBe("awaiting");
    expect(state.mode).toBe("deep");
    expect(state.plan?.steps).toHaveLength(2);
    expect(state.pending).toEqual({
      kind: "plan",
      plan: PLAN,
      estimate: { typical: 6, max: 15 },
      deepRemaining: 4,
      expiresAt: "2026-10-23T08:00:00Z",
    });
    expect(state.counts.model).toBe(2);
    expect(state.active).toBeNull();
    expect(state.lastId).toBe("6-0");
  });

  it("resumes after the decision, logs plan steps and ends with the report", () => {
    const paused = run(events(BEFORE_DECISION));
    const state = run(events(AFTER_DECISION, 7), paused);
    expect(state.pending).toBeNull();
    expect(state.decision).toBe("approve");
    expect(state.phase).toBe("done");
    expect(state.counts).toEqual({ search: 0, facts: 8, model: 3 });
    const steps = state.steps.filter((s) => s.tool === "step");
    expect(steps.map((s) => [s.stepId, s.status])).toEqual([
      ["s1", "done"],
      ["s2", "done"],
    ]);
    expect(state.plan?.steps.map((s) => s.status)).toEqual(["done", "done"]);
    expect(state.answer).toEqual(REPORT);
  });

  it("drops a node's earlier attempt when it re-runs after a resume", () => {
    const first: RunEvent[] = [
      {
        type: "step_started",
        id: "1-0",
        data: { step_id: "s1", summary: "a", node: "retriever", attempt: 1 },
      },
      {
        type: "tool_called",
        id: "2-0",
        data: {
          tool: "get_financial_facts",
          summary: "x",
          step_id: "s1",
          node: "retriever",
          attempt: 1,
        },
      },
      {
        type: "tool_result",
        id: "3-0",
        data: {
          tool: "get_financial_facts",
          count: 4,
          step_id: "s1",
          node: "retriever",
          attempt: 1,
        },
      },
      {
        type: "model_called",
        id: "4-0",
        data: {
          task_class: "cheap",
          provider: "g",
          cache_hit: false,
          fallback_from: null,
          node: "retriever",
          attempt: 1,
        },
      },
    ];
    const again: RunEvent[] = [
      {
        type: "run_resumed",
        id: "5-0",
        data: {
          from_node: "retriever",
          resume_count: 1,
          node: null,
          attempt: 1,
        },
      },
      {
        type: "step_started",
        id: "6-0",
        data: { step_id: "s1", summary: "a", node: "retriever", attempt: 2 },
      },
      {
        type: "tool_called",
        id: "7-0",
        data: {
          tool: "get_financial_facts",
          summary: "x",
          step_id: "s1",
          node: "retriever",
          attempt: 2,
        },
      },
      {
        type: "tool_result",
        id: "8-0",
        data: {
          tool: "get_financial_facts",
          count: 3,
          step_id: "s1",
          node: "retriever",
          attempt: 2,
        },
      },
      {
        type: "model_called",
        id: "9-0",
        data: {
          task_class: "cheap",
          provider: "g",
          cache_hit: false,
          fallback_from: null,
          node: "retriever",
          attempt: 2,
        },
      },
    ];
    const state = run([...first, ...again]);
    expect(state.counts).toEqual({ search: 0, facts: 3, model: 1 });
    expect(state.steps.map((s) => s.attempt)).toEqual([2, 2]);
  });

  it("ends on cancel and expiry", () => {
    const paused = run(events(BEFORE_DECISION));
    const cancelled = run(
      events(
        [
          ["decision_made", { action: "cancel", node: "decision", attempt: 1 }],
          ["run_cancelled", { node: "decision", attempt: 1 }],
        ],
        7,
      ),
      paused,
    );
    expect(cancelled.phase).toBe("cancelled");
    expect(cancelled.decision).toBe("cancel");
    const expired = reduceRun(paused, {
      type: "run_expired",
      id: "0-1",
      data: { node: null, attempt: 1 },
    });
    expect(expired.phase).toBe("expired");
    expect(expired.pending).toBeNull();
  });

  it("keeps single-engine events (no node or attempt) working", () => {
    const state = run([
      { type: "run_started", id: "1-0", data: { run_id: "r", language: "tr" } },
      {
        type: "tool_called",
        id: "2-0",
        data: { tool: "search_documents", summary: "THYAO" },
      },
      {
        type: "tool_result",
        id: "3-0",
        data: { tool: "search_documents", count: 6 },
      },
    ]);
    expect(state.counts.search).toBe(6);
    expect(state.steps[0]).toMatchObject({
      node: null,
      attempt: 1,
      stepId: null,
    });
  });
});
