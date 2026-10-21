import { describe, expect, it } from "vitest";

import { leadFigure, sourceCode } from "@/lib/runState";
import { errorMessage } from "@/lib/messages";
import { tr } from "@/i18n/tr";

import { CITATIONS } from "./fixtures";

describe("leadFigure", () => {
  it.each([
    ["Net kâr 1.234,5 milyon TL", "1.234,5"],
    ["Yıllık artış %23,5", "%23,5"],
    ["Revenue grew 14% in 2024", "14%"],
    ["2025 3. çeyrek net kâr 9.000 [1]", "9.000"],
    ["Revenue was $350.0 billion.", "$350.0"],
    ["2025-Q3 sonucu açıklandı", null],
    ["Rakam yok", null],
    ["30.10.2025 tarihli ihaleye 17 şirket katıldı", "17"],
  ])("%s", (text, figure) => {
    expect(leadFigure(text)).toBe(figure);
  });
});

describe("sourceCode", () => {
  it("reads like a board code", () => {
    expect(sourceCode(CITATIONS[0]!)).toBe("THYAO 2025-Q3");
    expect(sourceCode(CITATIONS[1]!)).toBe("THYAO 2025-Q3 S.3");
  });
});

describe("errorMessage", () => {
  it.each([
    ["unauthorized", tr.errorUnauthorized],
    ["beta_closed", tr.errorBetaClosed],
    ["quota_exhausted", tr.errorQuota],
    ["rate_limited", tr.errorRateLimited],
    ["providers_unavailable", tr.errorProviders],
    ["not_found", tr.errorNotFound],
    // Only the ask box knows a validation error means "too short"; elsewhere it is generic.
    ["validation_error", tr.errorGeneric],
    ["interrupted", tr.errorGeneric],
    [null, tr.errorGeneric],
  ])("%s", (code, text) => {
    expect(errorMessage(code, tr)).toBe(text);
  });
});

describe("labels", () => {
  it("never shows snake_case", async () => {
    const { humanize, metricLabel, docTypeLabel } =
      await import("@/i18n/labels");
    expect(humanize("THYAO 2025-Q3 net_income", "tr")).toBe(
      "THYAO 2025-Q3 Net dönem kârı",
    );
    expect(humanize("AAPL free_cash_flow", "en")).toBe("AAPL free cash flow");
    expect(metricLabel("revenue", "en")).toBe("Revenue");
    expect(docTypeLabel("10-K", "tr")).toBe("10-K yıllık rapor");
    expect(docTypeLabel("new_type", "en")).toBe("new type");
  });
});
