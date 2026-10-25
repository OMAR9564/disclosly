import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CitationPopover } from "@/components/CitationPopover";
import { formatDecimal } from "@/components/report/CalculationPopover";
import { ReportView } from "@/components/report/ReportView";
import type { Dictionary } from "@/i18n";
import { en } from "@/i18n/en";
import { tr } from "@/i18n/tr";
import type { Citation, Report } from "@/lib/api";

import { DEEP_CITATIONS, REPORT } from "../../e2e/fixtures/deep";

const FACTS = DEEP_CITATIONS as Citation[];
const CALC: Citation = {
  cite_id: "k:1",
  excerpt: "Toplam borç 2025-Q4 / 2025-Q1 - 1 (debt_growth)",
  source: {
    kind: "calculation",
    name: "debt_growth",
    inputs: [
      { name: "q1", cite_id: "f:80", value: "35100000000" },
      { name: "q4", cite_id: "f:83", value: "41200000000" },
      { name: "missing", cite_id: "f:999", value: "1" },
    ],
    code: "result = inputs['q4'] / inputs['q1'] - 1",
    output: "0.1737891737891738",
  },
};
const byId = new Map([...FACTS, CALC].map((c) => [c.cite_id, c]));

function plate(t: Dictionary = tr) {
  return render(
    <CitationPopover
      citation={CALC}
      marker={3}
      t={t}
      onClose={() => undefined}
      byId={byId}
    />,
  );
}

describe("CalculationPopover", () => {
  it("shows the formula, the result and the inputs with their values", () => {
    plate();
    const calc = screen.getByTestId("calculation");
    expect(calc.textContent).toContain(
      "Toplam borç 2025-Q4 / 2025-Q1 - 1 (debt_growth)",
    );
    expect(screen.getByTestId("calculation-output").textContent).toBe(
      "0,173789",
    );
    const items = within(calc).getAllByRole("listitem");
    expect(items[0]!.textContent).toContain("35.100.000.000");
    expect(items[1]!.textContent).toContain("41.200.000.000");
    // An input whose citation is not in the run shows no source key.
    expect(within(items[2]!).queryByRole("button")).toBeNull();
  });

  it("opens an input's own source in place", () => {
    plate();
    const key = screen.getByRole("button", { name: "THYAO 2025-Q1" });
    expect(key.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(key);
    expect(key.getAttribute("aria-expanded")).toBe("true");
    const cited = screen.getByTestId("calculation-input-source");
    expect(cited.textContent).toContain(
      "Toplam borç (2025-Q1): 35,1 milyar TRY",
    );
    expect(cited.textContent).toContain("Toplam borç · 2025-Q1");
    expect(within(cited).getByRole("link").getAttribute("href")).toBe(
      "https://www.kap.org.tr/tr/Bildirim/80",
    );
    fireEvent.click(key);
    expect(screen.queryByTestId("calculation-input-source")).toBeNull();
  });

  it("keeps the code behind a toggle", () => {
    plate(en);
    const toggle = screen.getByText("Show the calculation code");
    const details = toggle.closest("details")!;
    expect(details.open).toBe(false);
    expect(details.textContent).toContain("inputs['q4'] / inputs['q1'] - 1");
  });

  it("opens from a k: marker in a report", () => {
    const report: Report = {
      ...(REPORT as Report),
      summary: "Borç yılda %17,4 arttı [1].",
      citations: [{ marker: 1, cite_id: "k:1" }],
    };
    render(<ReportView answer={report} citations={[...FACTS, CALC]} t={tr} />);
    fireEvent.click(screen.getByRole("button", { name: "Kaynağı göster 1" }));
    expect(screen.getByTestId("calculation")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "THYAO 2025-Q4" }));
    expect(
      screen.getByTestId("calculation-input-source").textContent,
    ).toContain("41,2 milyar TRY");
  });

  it("formats decimals in the board's language", () => {
    expect(formatDecimal("1234.5", "tr")).toBe("1.234,5");
    expect(formatDecimal("1234.5", "en")).toBe("1,234.5");
    expect(formatDecimal("abc", "tr")).toBe("abc");
  });
});
