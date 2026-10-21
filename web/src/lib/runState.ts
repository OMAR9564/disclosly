import type { Answer, AnswerStatus, Citation } from "@/lib/api";
import type { RunEvent } from "@/lib/stream";

/** Pure reducer from run events to what the board shows (contracts/sse-events.md). */

export type Stage = "search" | "facts" | "model" | "check";

export const STAGES: Stage[] = ["search", "facts", "model", "check"];

export interface StepLog {
  id: string;
  tool: string;
  summary: string;
  count: number | null;
}

export interface RunState {
  phase: "connecting" | "running" | "done" | "error";
  counts: Record<Exclude<Stage, "check">, number>;
  active: Stage | null;
  steps: StepLog[];
  status: AnswerStatus | null;
  answer: Answer | null;
  citations: Citation[];
  errorCode: string | null;
}

export const initialRunState: RunState = {
  phase: "connecting",
  counts: { search: 0, facts: 0, model: 0 },
  active: null,
  steps: [],
  status: null,
  answer: null,
  citations: [],
  errorCode: null,
};

function toolStage(tool: string): "search" | "facts" {
  return tool === "get_financial_facts" ? "facts" : "search";
}

export function reduceRun(state: RunState, event: RunEvent): RunState {
  switch (event.type) {
    case "run_started":
      return { ...state, phase: "running" };
    case "tool_called":
      return {
        ...state,
        phase: "running",
        active: toolStage(event.data.tool),
        steps: [
          ...state.steps,
          {
            id: event.id,
            tool: event.data.tool,
            summary: event.data.summary,
            count: null,
          },
        ],
      };
    case "tool_result": {
      const stage = toolStage(event.data.tool);
      const steps = [...state.steps];
      const open = steps.findLastIndex(
        (s) => s.tool === event.data.tool && s.count === null,
      );
      if (open >= 0) steps[open] = { ...steps[open]!, count: event.data.count };
      return {
        ...state,
        steps,
        counts: {
          ...state.counts,
          [stage]: state.counts[stage] + event.data.count,
        },
      };
    }
    case "model_called":
      return {
        ...state,
        phase: "running",
        active: "model",
        counts: { ...state.counts, model: state.counts.model + 1 },
      };
    case "answer_delta":
      return state;
    case "final":
      return {
        ...state,
        phase: "done",
        active: "check",
        status: event.data.status,
        answer: event.data.answer,
        citations: event.data.citations,
      };
    case "error":
      return {
        ...state,
        phase: "error",
        active: null,
        errorCode: event.data.code,
      };
  }
}

/** First figure in a sentence, e.g. "1.234,5" or "%23,5", for the arrival row's flaps. */
export function leadFigure(text: string): string | null {
  const cleaned = text
    .replace(/\[\d+\]/g, " ")
    .replace(/\b\d{1,2}[./]\d{1,2}[./](19|20)\d{2}\b/g, " ")
    .replace(/\b(19|20)\d{2}-\d{2}-\d{2}\b/g, " ")
    .replace(/\b(19|20)\d{2}(-(Q[1-4]|FY))?\b/g, " ")
    .replace(/\b[1-4]\.\s?çeyrek/gi, " ");
  const match = /%\s?\d[\d.,]*|\$?\d[\d.,]*\s?%?/.exec(cleaned);
  return match ? match[0].replace(/\s/g, "").replace(/[.,]+$/, "") : null;
}

/** Board code for a source, like a flight number: "THYAO 2025-Q3 S.3". */
export function sourceCode(citation: Citation): string {
  const { ticker, period, page } = citation.source;
  return [ticker, period, page ? `S.${page}` : null].filter(Boolean).join(" ");
}
