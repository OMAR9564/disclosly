/** Synthetic run used by the mocked-API e2e tests and the local preview server. */

export const RUN_ID = "7d1c9a52-0000-4000-8000-000000000001";
export const QUESTION = "THYAO 2025 üçüncü çeyrek net kârı ne kadar?";

const citations = [
  {
    cite_id: "f:501",
    excerpt: "Net dönem kârı (2025-Q3): 1.234,5 milyon TRY",
    source: {
      kind: "fact",
      document_id: 10,
      ticker: "THYAO",
      doc_type: "kap_financial_report",
      period: "2025-Q3",
      metric: "net_income",
      source_url: "https://www.kap.org.tr/tr/Bildirim/1",
    },
  },
  {
    cite_id: "c:abc",
    excerpt:
      "Net dönem kârı 2025 yılının üçüncü çeyreğinde bir önceki yılın aynı dönemine göre %23,5 artışla 1.234,5 milyon TL oldu.",
    source: {
      kind: "chunk",
      document_id: 10,
      ticker: "THYAO",
      doc_type: "kap_financial_report",
      period: "2025-Q3",
      section: "Finansal Durum",
      page: 3,
      source_url: "https://www.kap.org.tr/tr/Bildirim/1",
    },
  },
];

export const ANSWER = {
  language: "tr",
  status: "completed",
  summary:
    "THYAO'nun 2025 3. çeyrek net kârı 1.234,5 milyon TL oldu [1]; bu, geçen yılın aynı dönemine göre %23,5 artış demek [2].",
  points: [
    { text: "Net dönem kârı 1.234,5 milyon TL", cite_ids: ["f:501"] },
    { text: "Yıllık artış %23,5", cite_ids: ["c:abc"] },
  ],
  citations: [
    { marker: 1, cite_id: "f:501" },
    { marker: 2, cite_id: "c:abc" },
  ],
  disclaimer: "Yatırım tavsiyesi değildir / Not investment advice",
};

export const EVENTS: [string, unknown][] = [
  ["run_started", { run_id: RUN_ID, language: "tr" }],
  [
    "model_called",
    {
      task_class: "cheap",
      provider: "groq",
      cache_hit: false,
      fallback_from: null,
    },
  ],
  [
    "tool_called",
    { tool: "get_financial_facts", summary: "THYAO 2025-Q3 net_income" },
  ],
  ["tool_result", { tool: "get_financial_facts", count: 1 }],
  [
    "tool_called",
    { tool: "search_documents", summary: "THYAO net dönem kârı artış" },
  ],
  ["tool_result", { tool: "search_documents", count: 6 }],
  [
    "model_called",
    {
      task_class: "strong",
      provider: "gemini",
      cache_hit: false,
      fallback_from: null,
    },
  ],
  ["final", { status: "completed", answer: ANSWER, citations }],
];

export function sseBody(events = EVENTS): string {
  return events
    .map(
      ([event, data], i) =>
        `id: ${i + 1}-0\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`,
    )
    .join("");
}

export const RUN = {
  run_id: RUN_ID,
  status: "completed",
  question: QUESTION,
  language: "tr",
  answer: ANSWER,
  citations,
  usage: {},
  error_code: null,
  created_at: "2026-10-21T08:00:00Z",
  finished_at: "2026-10-21T08:00:09Z",
};
