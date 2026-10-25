import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ReportView } from "@/components/report/ReportView";
import { en } from "@/i18n/en";
import { tr } from "@/i18n/tr";
import type { Citation, Report } from "@/lib/api";

import { DEEP_CITATIONS, REPORT } from "../../e2e/fixtures/deep";

const report = REPORT as Report;
const citations = DEEP_CITATIONS as Citation[];

describe("ReportView", () => {
  it("renders summary, confidence, points, chart and table", () => {
    render(<ReportView answer={report} citations={citations} t={tr} />);
    expect(screen.getByTestId("answer-summary").textContent).toContain(
      "41,2 milyar TL",
    );
    expect(screen.getByTestId("report-confidence").textContent).toContain(
      "Güven: Yüksek.",
    );
    expect(screen.getByTestId("report-chart")).toBeTruthy();
    const table = screen.getByTestId("report-table");
    expect(within(table).getByRole("heading").textContent).toBe(
      "THYAO toplam borç ve nakit, 2025 çeyrekleri",
    );
    expect(within(table).getAllByRole("row")).toHaveLength(5);
    expect(screen.queryByTestId("report-unverified")).toBeNull();
    expect(screen.getByTestId("answer-disclaimer").textContent).toBe(
      "Yatırım tavsiyesi değildir",
    );
  });

  it("numbers cited cells after the report's own markers and opens their source", () => {
    render(<ReportView answer={report} citations={citations} t={tr} />);
    const table = screen.getByTestId("report-table");
    const markers = within(table)
      .getAllByRole("button")
      .map((b) => b.textContent);
    expect(markers).toEqual(["3", "4", "5", "6", "7", "8", "1", "2"]);

    const cell = within(table).getByRole("button", {
      name: "Kaynağı göster 1: 41,2 milyar TL",
    });
    fireEvent.click(cell);
    expect(cell.getAttribute("aria-expanded")).toBe("true");
    expect(within(table).getByTestId("citation").textContent).toContain(
      "Toplam borç (2025-Q4): 41,2 milyar TRY",
    );
    fireEvent.click(cell);
    expect(within(table).queryByTestId("citation")).toBeNull();
  });

  it("lists what could not be verified with the partial banner", () => {
    render(
      <ReportView
        answer={{
          ...report,
          status: "partial",
          confidence: { level: "low", note: "Bir çeyrek eksik." },
          unverified: ["2025-Q2 nakit doğrulanamadı"],
        }}
        citations={citations}
        t={en}
      />,
    );
    expect(screen.getByRole("note").textContent).toBe(en.bannerPartial);
    expect(screen.getByTestId("report-unverified").textContent).toContain(
      "2025-Q2 nakit doğrulanamadı",
    );
    expect(screen.getByTestId("report-confidence").textContent).toContain(
      "Confidence: Low.",
    );
  });

  it("renders a Phase 3 answer without the new parts", () => {
    const { tables, chart, confidence, unverified, ...old } = report;
    void [tables, chart, confidence, unverified];
    render(<ReportView answer={old} citations={citations} t={tr} />);
    expect(screen.queryByTestId("report-table")).toBeNull();
    expect(screen.queryByTestId("report-chart")).toBeNull();
    expect(screen.queryByTestId("report-confidence")).toBeNull();
    expect(screen.getByTestId("answer-summary")).toBeTruthy();
  });

  it("shows a calculation citation with its formula, inputs and result", () => {
    const calc: Citation = {
      cite_id: "k:1",
      excerpt: "Net borç / FAVÖK = net_debt / ebitda",
      source: {
        kind: "calculation",
        name: "net_debt_to_ebitda",
        inputs: [{ name: "net_debt", cite_id: "f:83", value: "41200000000" }],
        code: "result = net_debt / ebitda",
        output: "1.9",
      },
    };
    render(
      <ReportView
        answer={{
          ...report,
          summary: "Oran 1,9 [1].",
          citations: [{ marker: 1, cite_id: "k:1" }],
        }}
        citations={[...citations, calc]}
        t={tr}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Kaynağı göster 1" }));
    const plate = screen.getByTestId("citation");
    expect(plate.textContent).toContain("net_debt_to_ebitda");
    expect(plate.textContent).toContain("Net borç / FAVÖK = net_debt / ebitda");
    expect(plate.textContent).toContain("41.200.000.000");
    expect(plate.textContent).toContain("1,9");
  });
});
