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
import type { RunEvent } from "@/lib/stream";

/** Pure reducer from run events to what the board shows (contracts/sse-events.md). */

export type Stage = "search" | "facts" | "model" | "check";

export const STAGES: Stage[] = ["search", "facts", "model", "check"];

export interface StepLog {
  id: string;
  /** A tool call ("search_documents", …) or "step" for a plan step the graph started. */
  tool: string;
  summary: string;
  count: number | null;
  node: string | null;
  stepId: string | null;
  attempt: number;
  status: StepStatus | null;
}

export interface PendingDecision {
  kind: DecisionKind;
  plan: Plan | null;
  estimate: Estimate | null;
  deepRemaining: number;
  expiresAt: string;
}

export type Phase =
  | "connecting"
  | "running"
  | "awaiting"
  | "done"
  | "error"
  | "cancelled"
  | "expired";

export interface RunState {
  phase: Phase;
  counts: Record<Exclude<Stage, "check">, number>;
  active: Stage | null;
  steps: StepLog[];
  /** Model calls by origin, so a node's re-run can drop the calls of its earlier attempt. */
  models: { node: string | null; attempt: number }[];
  mode: "simple" | "deep" | "refused" | null;
  plan: Plan | null;
  pending: PendingDecision | null;
  decision: DecisionAction | null;
  status: AnswerStatus | null;
  answer: Report | null;
  citations: Citation[];
  errorCode: string | null;
  /** Times the server resumed this run after a restart (`run_resumed`). */
  resumes: number;
  /** Id of the newest event handled; a reconnect skips events up to it. */
  lastId: string | null;
}

export const initialRunState: RunState = {
  phase: "connecting",
  counts: { search: 0, facts: 0, model: 0 },
  active: null,
  steps: [],
  models: [],
  mode: null,
  plan: null,
  pending: null,
  decision: null,
  status: null,
  answer: null,
  citations: [],
  errorCode: null,
  resumes: 0,
  lastId: null,
};

function toolStage(tool: string): "search" | "facts" {
  return tool === "get_financial_facts" ? "facts" : "search";
}

function counts(
  steps: StepLog[],
  models: RunState["models"],
): RunState["counts"] {
  const total = { search: 0, facts: 0, model: models.length };
  for (const step of steps) {
    if (step.tool === "step" || step.count === null) continue;
    total[toolStage(step.tool)] += step.count;
  }
  return total;
}

/**
 * A node re-ran after a resume: drop what its earlier attempts reported for the same plan
 * step (contracts/sse-events.md), so counts and the log show one execution.
 */
function supersede(
  state: RunState,
  node: string | null | undefined,
  stepId: string | null | undefined,
  attempt: number | undefined,
): Pick<RunState, "steps" | "models"> {
  if (!node || !attempt || attempt <= 1)
    return { steps: state.steps, models: state.models };
  const step = stepId ?? null;
  return {
    steps: state.steps.filter(
      (s) => !(s.node === node && s.stepId === step && s.attempt < attempt),
    ),
    models:
      step === null
        ? state.models.filter((m) => !(m.node === node && m.attempt < attempt))
        : state.models,
  };
}

function withSteps(
  state: RunState,
  steps: StepLog[],
  models: RunState["models"] = state.models,
): RunState {
  return { ...state, steps, models, counts: counts(steps, models) };
}

function apply(state: RunState, event: RunEvent): RunState {
  const { node, attempt } = event.data;
  switch (event.type) {
    case "run_started":
      return { ...state, phase: "running" };
    case "run_resumed":
      // The re-run node's events arrive with a higher attempt and replace the earlier ones.
      return {
        ...state,
        phase: "running",
        pending: null,
        resumes: event.data.resume_count,
      };
    case "decision_made":
      return {
        ...state,
        phase: "running",
        pending: null,
        decision: event.data.action,
      };
    case "mode_selected":
      return { ...state, phase: "running", mode: event.data.mode };
    case "plan_proposed":
      return { ...state, plan: event.data.plan };
    case "awaiting_decision":
      return {
        ...state,
        phase: "awaiting",
        active: null,
        plan: event.data.plan ?? state.plan,
        pending: {
          kind: event.data.kind,
          plan: event.data.plan,
          estimate: event.data.estimate,
          deepRemaining: event.data.deep_remaining,
          expiresAt: event.data.expires_at,
        },
      };
    case "step_started": {
      const kept = supersede(state, node, event.data.step_id, attempt);
      return withSteps(
        { ...state, phase: "running" },
        [
          ...kept.steps,
          {
            id: event.id,
            tool: "step",
            summary: event.data.summary,
            count: null,
            node: node ?? null,
            stepId: event.data.step_id,
            attempt: attempt ?? 1,
            status: "pending",
          },
        ],
        kept.models,
      );
    }
    case "step_finished": {
      const steps = [...state.steps];
      const open = steps.findLastIndex(
        (s) => s.tool === "step" && s.stepId === event.data.step_id,
      );
      if (open >= 0)
        steps[open] = { ...steps[open]!, status: event.data.status };
      const plan = state.plan && {
        ...state.plan,
        steps: state.plan.steps.map((s) =>
          s.id === event.data.step_id ? { ...s, status: event.data.status } : s,
        ),
      };
      return withSteps({ ...state, plan }, steps);
    }
    case "tool_called": {
      const stepId = event.data.step_id ?? null;
      const kept = supersede(state, node, stepId, attempt);
      return withSteps(
        { ...state, phase: "running", active: toolStage(event.data.tool) },
        [
          ...kept.steps,
          {
            id: event.id,
            tool: event.data.tool,
            summary: event.data.summary,
            count: null,
            node: node ?? null,
            stepId,
            attempt: attempt ?? 1,
            status: null,
          },
        ],
        kept.models,
      );
    }
    case "tool_result": {
      const steps = [...state.steps];
      const open = steps.findLastIndex(
        (s) => s.tool === event.data.tool && s.count === null,
      );
      if (open >= 0) steps[open] = { ...steps[open]!, count: event.data.count };
      return withSteps(state, steps);
    }
    case "model_called": {
      const kept = supersede(state, node, null, attempt);
      return withSteps(
        { ...state, phase: "running", active: "model" },
        kept.steps,
        [...kept.models, { node: node ?? null, attempt: attempt ?? 1 }],
      );
    }
    case "calculation":
    case "verification":
    case "memory_updated":
    case "answer_delta":
      return state;
    case "run_cancelled":
      return { ...state, phase: "cancelled", active: null, pending: null };
    case "run_expired":
      return { ...state, phase: "expired", active: null, pending: null };
    case "final":
      return {
        ...state,
        phase: "done",
        active: "check",
        pending: null,
        status: event.data.status,
        answer: event.data.answer,
        citations: event.data.citations,
      };
    case "error":
      return {
        ...state,
        phase: "error",
        active: null,
        pending: null,
        errorCode: event.data.code,
      };
  }
}

export function reduceRun(state: RunState, event: RunEvent): RunState {
  return { ...apply(state, event), lastId: event.id || state.lastId };
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
  if (citation.source.kind === "calculation") return citation.source.name;
  const { ticker, period, page } = citation.source;
  return [ticker, period, page ? `S.${page}` : null].filter(Boolean).join(" ");
}
