import type { Answer, Citation } from "@/lib/api";

export const CITATIONS: Citation[] = [
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
    excerpt: "Net dönem kârı %23,5 artışla 1.234,5 milyon TL oldu.",
    source: {
      kind: "chunk",
      document_id: 10,
      ticker: "THYAO",
      doc_type: "kap_financial_report",
      period: "2025-Q3",
      section: "Finansal Durum",
      page: 3,
      source_url: "https://www.kap.org.tr/tr/Bildirim/2",
    },
  },
];

export const ANSWER: Answer = {
  language: "tr",
  status: "completed",
  summary: "Net kâr 1.234,5 milyon TL oldu [1]; artış %23,5 [2].",
  points: [
    { text: "Net dönem kârı 1.234,5 milyon TL", cite_ids: ["f:501"] },
    { text: "Yıllık artış %23,5", cite_ids: ["c:abc"] },
    { text: "Belgede tarih bilgisi yok", cite_ids: [] },
  ],
  citations: [
    { marker: 1, cite_id: "f:501" },
    { marker: 2, cite_id: "c:abc" },
  ],
  disclaimer: "Yatırım tavsiyesi değildir / Not investment advice",
};
