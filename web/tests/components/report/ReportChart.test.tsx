import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ReportChart } from "@/components/report/ReportChart";
import { en } from "@/i18n/en";
import { tr } from "@/i18n/tr";
import type { Chart, Table } from "@/lib/api";
import { chartData, niceTicks, parseFigure } from "@/lib/figures";

import { REPORT } from "../../e2e/fixtures/deep";

const TABLES = REPORT.tables as Table[];
const LINE = REPORT.chart as Chart;

function plotted(series: string) {
  return [
    ...document.querySelectorAll(`[data-series="${series}"] [data-value]`),
  ].map((node) => Number(node.getAttribute("data-value")));
}

describe("parseFigure", () => {
  it.each([
    ["41,2 milyar TL", "tr", 41.2e9],
    ["%23,5", "tr", 23.5],
    ["-140.454.000 TRY", "tr", -140454000],
    ["8.401.456.000", "tr", 8401456000],
    ["41.2 milyar", "tr", 41.2e9],
    ["1,9", "tr", 1.9],
    ["$1,234.5 million", "en", 1.2345e9],
    ["(350)", "en", -350],
    ["2025-Q3", "tr", 2025],
    ["yok", "tr", null],
    ["1,2,3", "en", 123],
    ["1.2.3,4,5", "tr", null],
  ] as const)("%s (%s)", (text, language, value) => {
    expect(parseFigure(text, language)).toBe(value);
  });
});

describe("niceTicks", () => {
  it("covers the range from zero with round steps", () => {
    expect(niceTicks(35.1e9, 41.2e9)).toEqual([0, 20e9, 40e9, 60e9]);
    expect(niceTicks(-3, 7)).toEqual([-5, -2.5, 0, 2.5, 5, 7.5]);
    expect(niceTicks(0, 0)).toEqual([0, 1]);
  });
});

describe("ReportChart", () => {
  it("plots line points equal to the table's values", () => {
    render(<ReportChart chart={LINE} tables={TABLES} t={tr} />);
    expect(screen.getByTestId("report-chart").dataset.kind).toBe("line");
    expect(plotted("Toplam borç")).toEqual([35.1e9, 36.4e9, 38.5e9, 41.2e9]);
    expect(plotted("Nakit")).toEqual([12.8e9, 11.9e9, 14.2e9, 15.6e9]);
    const fallback = screen.getByRole("table", { hidden: true });
    expect(fallback.textContent).toContain("41,2 milyar TL");
  });

  it("draws bars with the same values", () => {
    render(
      <ReportChart chart={{ ...LINE, kind: "bar" }} tables={TABLES} t={tr} />,
    );
    expect(screen.getByTestId("report-chart").dataset.kind).toBe("bar");
    expect(document.querySelectorAll(".chart__bars rect")).toHaveLength(8);
    expect(plotted("Nakit")).toEqual([12.8e9, 11.9e9, 14.2e9, 15.6e9]);
  });

  it("hides series without numbers and draws nothing when none is left", () => {
    const table: Table = {
      title: "t",
      columns: ["Dönem", "Borç", "Not"],
      rows: [
        [{ text: "2025-Q1" }, { text: "35,1" }, { text: "—" }],
        [{ text: "2025-Q2" }, { text: "yok" }, { text: "—" }],
      ],
    };
    const chart: Chart = {
      kind: "line",
      table: 0,
      x_column: "Dönem",
      series: ["Borç", "Not"],
    };
    const data = chartData(chart, [table], "tr");
    expect(data?.series.map((s) => s.name)).toEqual(["Borç"]);
    expect(data?.series[0]?.points[1]).toBeNull();
    expect(chartData({ ...chart, series: ["Not"] }, [table], "tr")).toBeNull();
    expect(chartData({ ...chart, table: 3 }, [table], "tr")).toBeNull();
    expect(chartData({ ...chart, x_column: "X" }, [table], "tr")).toBeNull();

    const { container } = render(
      <ReportChart
        chart={{ ...chart, series: ["Not"] }}
        tables={[table]}
        t={tr}
      />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("moves the readout with the arrow keys and reads the cells' own text", () => {
    render(<ReportChart chart={LINE} tables={TABLES} t={en} />);
    const slider = screen.getByRole("slider");
    expect(slider.getAttribute("aria-valuenow")).toBe("4");
    expect(slider.getAttribute("aria-valuetext")).toBe(
      "2025-Q4; Toplam borç: 41,2 milyar TL; Nakit: 15,6 milyar TL",
    );
    fireEvent.keyDown(slider, { key: "Home" });
    expect(slider.getAttribute("aria-valuenow")).toBe("1");
    fireEvent.keyDown(slider, { key: "ArrowRight" });
    expect(screen.getByTestId("chart-readout").textContent).toContain(
      "36,4 milyar TL",
    );
    fireEvent.keyDown(slider, { key: "ArrowLeft" });
    fireEvent.keyDown(slider, { key: "ArrowLeft" });
    expect(slider.getAttribute("aria-valuenow")).toBe("1");
    fireEvent.keyDown(slider, { key: "End" });
    expect(slider.getAttribute("aria-valuenow")).toBe("4");
    fireEvent.keyDown(slider, { key: "a" });
    fireEvent.blur(slider);
    expect(slider.getAttribute("aria-valuenow")).toBe("4");
  });

  it("follows the pointer", () => {
    render(<ReportChart chart={LINE} tables={TABLES} t={tr} />);
    const slider = screen.getByRole("slider");
    slider.getBoundingClientRect = () =>
      ({ left: 0, width: 640, top: 0, height: 260 }) as DOMRect;
    fireEvent.pointerMove(slider, { clientX: 120 });
    expect(slider.getAttribute("aria-valuenow")).toBe("1");
    fireEvent.pointerDown(slider, { clientX: 600 });
    expect(slider.getAttribute("aria-valuenow")).toBe("4");
    fireEvent.pointerLeave(slider);
    expect(slider.getAttribute("aria-valuenow")).toBe("4");
  });
});
