import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Timeline } from "@/components/Timeline";
import { en } from "@/i18n/en";
import { tr } from "@/i18n/tr";
import { type RunState, initialRunState, reduceRun } from "@/lib/runState";
import type { RunEvent } from "@/lib/stream";

import { ANSWER, CITATIONS } from "./fixtures";

const EVENTS: RunEvent[] = [
  { type: "run_started", id: "1", data: { run_id: "r", language: "tr" } },
  {
    type: "model_called",
    id: "2",
    data: {
      task_class: "cheap",
      provider: "groq",
      cache_hit: false,
      fallback_from: null,
    },
  },
  {
    type: "tool_called",
    id: "3",
    data: { tool: "get_financial_facts", summary: "THYAO 2025-Q3 net_income" },
  },
  {
    type: "tool_result",
    id: "4",
    data: { tool: "get_financial_facts", count: 2 },
  },
  {
    type: "tool_called",
    id: "5",
    data: { tool: "search_documents", summary: "THYAO net kâr" },
  },
];

function play(events: RunEvent[]): RunState {
  return events.reduce(reduceRun, initialRunState);
}

function cell(name: string): string {
  const item = document.querySelector(`[data-stage="${name}"]`)!;
  return item.querySelector(".sr-only")!.textContent ?? "";
}

describe("Timeline", () => {
  it("shows connecting before any event", () => {
    render(<Timeline state={initialRunState} t={tr} />);
    expect(screen.getByRole("status").textContent).toBe(tr.runConnecting);
    expect(cell("search")).toBe(`${tr.stageSearch}: —`);
  });

  it("counts stages and lists steps while running", () => {
    render(<Timeline state={play(EVENTS)} t={tr} />);
    expect(cell("facts")).toBe(`${tr.stageFacts}: 2`);
    expect(cell("model")).toBe(`${tr.stageModel}: 1`);
    expect(cell("search")).toBe(`${tr.stageSearch}: 0`);
    const live = document.querySelector('[data-live="true"]')!;
    expect(live.getAttribute("data-stage")).toBe("search");
    expect(screen.getByText("THYAO 2025-Q3 Net dönem kârı")).toBeTruthy();
    expect(screen.getByText(`2 ${tr.stepResults}`)).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe(tr.runRunning);
  });

  it.each([
    ["completed", en.checkCompleted, "true"],
    ["partial", en.checkPartial, "false"],
    ["refused", en.checkRefused, "false"],
    ["no_evidence", en.checkNoEvidence, "false"],
  ] as const)("shows the check word for %s", (status, word, live) => {
    const state = play([
      ...EVENTS,
      {
        type: "final",
        id: "9",
        data: { status, answer: { ...ANSWER, status }, citations: CITATIONS },
      },
    ]);
    render(<Timeline state={state} t={en} />);
    expect(cell("check")).toBe(`${en.stageCheck}: ${word}`);
    expect(
      document.querySelector('[data-stage="check"]')!.getAttribute("data-live"),
    ).toBe(live);
    expect(screen.getByRole("status").textContent).toBe(en.runDone);
    const log = document.querySelector("details")!;
    expect(log.open).toBe(false);
    expect(log.querySelector("summary")!.textContent).toBe(
      `2 ${en.stepsCount}`,
    );
  });

  it("shows an error word and no status note after an error", () => {
    const state = play([
      ...EVENTS,
      {
        type: "error",
        id: "9",
        data: { code: "providers_unavailable", message: "x" },
      },
    ]);
    render(<Timeline state={state} t={tr} />);
    expect(cell("check")).toBe(`${tr.stageCheck}: ${tr.boardError}`);
    expect(screen.queryByRole("status")).toBeNull();
    expect(state.errorCode).toBe("providers_unavailable");
  });
});
