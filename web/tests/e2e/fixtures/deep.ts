import type { Page } from "@playwright/test";

import { sseBody } from "./run";

/** Synthetic deep run used by the mocked-API tests: a plan, an approval, then a report. */

export const DEEP_RUN_ID = "7d1c9a52-0000-4000-8000-000000000002";
export const DEEP_QUESTION =
  "THYAO'nun toplam borcu ve nakdi 2025 çeyrekleri boyunca nasıl değişti?";

const PERIODS = ["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"];
const DEBT = ["35,1", "36,4", "38,5", "41,2"];
const CASH = ["12,8", "11,9", "14,2", "15,6"];

const fact = (id: number, period: string, metric: string, text: string) => ({
  cite_id: `f:${id}`,
  excerpt: text,
  source: {
    kind: "fact",
    document_id: 20 + id,
    ticker: "THYAO",
    doc_type: "kap_financial_report",
    period,
    metric,
    source_url: `https://www.kap.org.tr/tr/Bildirim/${id}`,
  },
});

export const DEEP_CITATIONS = [
  ...PERIODS.map((p, i) =>
    fact(80 + i, p, "total_debt", `Toplam borç (${p}): ${DEBT[i]} milyar TRY`),
  ),
  ...PERIODS.map((p, i) =>
    fact(
      90 + i,
      p,
      "cash_and_equivalents",
      `Nakit ve nakit benzerleri (${p}): ${CASH[i]} milyar TRY`,
    ),
  ),
];

export const PLAN = {
  steps: [
    {
      id: "s1",
      kind: "facts",
      goal: "THYAO toplam borcu, 2025'in dört çeyreği",
      tickers: ["THYAO"],
      periods: PERIODS,
      metrics: ["total_debt"],
      status: "pending",
    },
    {
      id: "s2",
      kind: "facts",
      goal: "THYAO nakit ve nakit benzerleri, aynı dönemler",
      tickers: ["THYAO"],
      periods: PERIODS,
      metrics: ["cash_and_equivalents"],
      status: "pending",
    },
  ],
  estimate: { typical: 6, max: 15 },
  edited: false,
  revision: 0,
};

export const REPORT = {
  language: "tr",
  status: "completed",
  summary:
    "THYAO'nun toplam borcu 2025 boyunca her çeyrek arttı ve 2025-Q4'te 41,2 milyar TL oldu [1]; nakit aynı çeyrekte 15,6 milyar TL [2].",
  points: [
    { text: "2025-Q1 toplam borç 35,1 milyar TL", cite_ids: ["f:80"] },
    { text: "2025-Q4 toplam borç 41,2 milyar TL", cite_ids: ["f:83"] },
  ],
  tables: [
    {
      title: "THYAO toplam borç ve nakit, 2025 çeyrekleri",
      columns: ["Dönem", "Toplam borç", "Nakit"],
      rows: PERIODS.map((p, i) => [
        { text: p },
        { text: `${DEBT[i]} milyar TL`, cite_id: `f:${80 + i}` },
        { text: `${CASH[i]} milyar TL`, cite_id: `f:${90 + i}` },
      ]),
    },
  ],
  chart: {
    kind: "line",
    table: 0,
    x_column: "Dönem",
    series: ["Toplam borç", "Nakit"],
  },
  confidence: {
    level: "high",
    note: "Dört çeyreğin tamamı KAP finansal raporlarında var.",
  },
  unverified: [],
  citations: [
    { marker: 1, cite_id: "f:83" },
    { marker: 2, cite_id: "f:93" },
  ],
  disclaimer: "Yatırım tavsiyesi değildir / Not investment advice",
};

const origin = (node: string | null, extra: object = {}) => ({
  node,
  attempt: 1,
  ...extra,
});

/** Events up to the pause; the server closes the stream after `awaiting_decision`. */
export const BEFORE_DECISION: [string, unknown][] = [
  ["run_started", origin(null, { run_id: DEEP_RUN_ID, language: "tr" })],
  [
    "model_called",
    origin("router", {
      task_class: "cheap",
      provider: "gemini",
      cache_hit: false,
      fallback_from: null,
    }),
  ],
  ["mode_selected", origin("router", { mode: "deep", uses_watchlist: false })],
  [
    "model_called",
    origin("planner", {
      task_class: "strong",
      provider: "gemini",
      cache_hit: false,
      fallback_from: null,
    }),
  ],
  ["plan_proposed", origin("planner", { plan: PLAN, revision: 0 })],
  [
    "awaiting_decision",
    origin("decision", {
      kind: "plan",
      plan: PLAN,
      estimate: PLAN.estimate,
      deep_remaining: 4,
      expires_at: "2026-10-23T08:00:00Z",
    }),
  ],
];

export const AFTER_DECISION: [string, unknown][] = [
  ["decision_made", origin("decision", { action: "approve" })],
  [
    "step_started",
    origin("retriever", { step_id: "s1", summary: PLAN.steps[0]!.goal }),
  ],
  [
    "tool_called",
    origin("retriever", {
      tool: "get_financial_facts",
      summary: "THYAO 2025 total_debt",
      step_id: "s1",
    }),
  ],
  [
    "tool_result",
    origin("retriever", {
      tool: "get_financial_facts",
      count: 4,
      step_id: "s1",
    }),
  ],
  ["step_finished", origin("retriever", { step_id: "s1", status: "done" })],
  [
    "step_started",
    origin("retriever", { step_id: "s2", summary: PLAN.steps[1]!.goal }),
  ],
  [
    "tool_called",
    origin("retriever", {
      tool: "get_financial_facts",
      summary: "THYAO 2025 cash_and_equivalents",
      step_id: "s2",
    }),
  ],
  [
    "tool_result",
    origin("retriever", {
      tool: "get_financial_facts",
      count: 4,
      step_id: "s2",
    }),
  ],
  ["step_finished", origin("retriever", { step_id: "s2", status: "done" })],
  [
    "model_called",
    origin("writer", {
      task_class: "strong",
      provider: "gemini",
      cache_hit: false,
      fallback_from: null,
    }),
  ],
  [
    "final",
    origin("writer", {
      status: "completed",
      answer: REPORT,
      citations: DEEP_CITATIONS,
    }),
  ],
];

export const DEEP_RUN = {
  run_id: DEEP_RUN_ID,
  status: "awaiting_approval",
  question: DEEP_QUESTION,
  language: "tr",
  answer: null,
  citations: [],
  usage: {},
  error_code: null,
  created_at: "2026-10-22T08:00:00Z",
  finished_at: null,
  engine: "graph",
  mode: "deep",
  plan: PLAN,
  plan_expires_at: "2026-10-23T08:00:00Z",
  decision: null,
};

export const QUOTAS = {
  simple: {
    limit: 20,
    used: 2,
    remaining: 18,
    resets_at: "2026-10-22T21:00:00Z",
  },
  deep: { limit: 5, used: 1, remaining: 4, resets_at: "2026-10-22T21:00:00Z" },
};

/** Mocks a deep run: plan pause, then the timeline and report after the decision. */
export async function mockDeepRun(
  page: Page,
): Promise<{ decisions: unknown[] }> {
  const decisions: unknown[] = [];
  let streams = 0;
  await page.route("**/healthz", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok", version: "abc1234" }),
    }),
  );
  await page.route("**/v1/auth/refresh", (route) =>
    route.fulfill({ status: 401, body: "{}" }),
  );
  await page.route(`**/v1/research/${DEEP_RUN_ID}/stream`, (route) => {
    streams += 1;
    // The first stream ends at the pause; after the decision a new EventSource cannot send
    // Last-Event-ID, so the server replays from the start and the client skips what it saw.
    const events =
      streams === 1 ? BEFORE_DECISION : [...BEFORE_DECISION, ...AFTER_DECISION];
    return route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: sseBody(events),
    });
  });
  await page.route(`**/v1/research/${DEEP_RUN_ID}/approve`, async (route) => {
    decisions.push(route.request().postDataJSON());
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({
        run_id: DEEP_RUN_ID,
        status: "running",
        quota: QUOTAS,
      }),
    });
  });
  await page.route(`**/v1/research/${DEEP_RUN_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(DEEP_RUN),
    }),
  );
  return { decisions };
}
