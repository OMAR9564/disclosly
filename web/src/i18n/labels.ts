import type { Language } from ".";

/** Readable names for API identifiers; the UI never shows snake_case. */
const METRICS: Record<string, Record<Language, string>> = {
  net_income: { tr: "Net dönem kârı", en: "Net income" },
  cash: { tr: "Nakit ve nakit benzerleri", en: "Cash and cash equivalents" },
  total_debt: { tr: "Toplam borç", en: "Total debt" },
  depreciation_amortization: {
    tr: "Amortisman",
    en: "Depreciation and amortization",
  },
  revenue: { tr: "Hasılat", en: "Revenue" },
  gross_profit: { tr: "Brüt kâr", en: "Gross profit" },
  operating_income: { tr: "Esas faaliyet kârı", en: "Operating income" },
  ebitda: { tr: "FAVÖK", en: "EBITDA" },
  net_debt: { tr: "Net borç", en: "Net debt" },
  total_assets: { tr: "Toplam varlıklar", en: "Total assets" },
  total_equity: { tr: "Özkaynaklar", en: "Total equity" },
  operating_cash_flow: { tr: "İşletme nakit akışı", en: "Operating cash flow" },
  eps: { tr: "Hisse başına kâr", en: "Earnings per share" },
};

const DOC_TYPES: Record<string, Record<Language, string>> = {
  "10-K": { tr: "10-K yıllık rapor", en: "10-K annual report" },
  "10-Q": { tr: "10-Q çeyrek rapor", en: "10-Q quarterly report" },
  kap_financial_report: {
    tr: "KAP finansal rapor",
    en: "KAP financial report",
  },
  kap_material_event: {
    tr: "KAP özel durum açıklaması",
    en: "KAP material event",
  },
  kap_annual_report: { tr: "KAP faaliyet raporu", en: "KAP annual report" },
  kap_prospectus: { tr: "KAP izahname", en: "KAP prospectus" },
  kap_fund_report: { tr: "KAP fon raporu", en: "KAP fund report" },
};

function plain(id: string): string {
  return id.replace(/_/g, " ");
}

export function metricLabel(metric: string, language: Language): string {
  return METRICS[metric]?.[language] ?? plain(metric);
}

export function docTypeLabel(docType: string, language: Language): string {
  return DOC_TYPES[docType]?.[language] ?? plain(docType);
}

/** Replace snake_case identifiers inside a step summary with readable names. */
export function humanize(text: string, language: Language): string {
  return text.replace(/\b[a-z]+(?:_[a-z]+)+\b/g, (id) =>
    metricLabel(id, language),
  );
}
