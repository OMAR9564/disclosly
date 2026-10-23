import type { Chart, Table } from "@/lib/api";

/** Reading numbers back out of report cells, for the chart (contracts/report.md rule 4). */

const SCALES: [RegExp, number][] = [
  [/\b(milyar|billion|bn)\b/i, 1e9],
  [/\b(milyon|million|mn)\b/i, 1e6],
  [/\b(bin|thousand)\b/i, 1e3],
];

/**
 * The value a cell shows: "41,2 milyar TL" → 41.2e9, "%23,5" → 23.5, "-140.454.000" →
 * -140454000, "$1,234.5 million" → 1.2345e9. Turkish cells use "." for thousands and "," for
 * decimals; English the reverse. A cell without a number is null.
 */
export function parseFigure(
  text: string,
  language: "tr" | "en",
): number | null {
  const match = /([-−–]?)\s*[$€£₺%]?\s*(\d[\d.,]*)/.exec(text);
  if (!match) return null;
  let digits = match[2]!.replace(/[.,]+$/, "");
  const [group, decimal] = language === "tr" ? [".", ","] : [",", "."];
  const parts = digits.split(decimal);
  if (parts.length > 2) return null;
  const whole = parts[0]!;
  // A lone group separator followed by other than three digits is a decimal written the
  // other way round ("41.2" in a Turkish report): read it as a decimal.
  const groups = whole.split(group);
  const grouped =
    groups.length > 1 && groups.slice(1).every((g) => g.length === 3);
  digits =
    groups.length > 1 && !grouped && parts.length === 1 && groups.length === 2
      ? `${groups[0]}.${groups[1]}`
      : `${groups.join("")}${parts[1] !== undefined ? `.${parts[1]}` : ""}`;
  const value = Number(digits);
  if (!Number.isFinite(value)) return null;
  const scale = SCALES.find(([word]) => word.test(text))?.[1] ?? 1;
  const negative = match[1] !== "" || /^\s*\(.*\)\s*$/.test(text);
  return (negative ? -1 : 1) * value * scale;
}

export interface ChartPoint {
  /** Row index in the table. */
  row: number;
  x: string;
  value: number;
  /** The cell's own text, shown in the readout as the report wrote it. */
  text: string;
}

export interface ChartSeries {
  name: string;
  points: (ChartPoint | null)[];
}

export interface ChartData {
  kind: Chart["kind"];
  xColumn: string;
  labels: string[];
  series: ChartSeries[];
}

/**
 * The plotted data, read from the chart's table cells (never sent separately). Series with no
 * numeric cell are dropped; null when nothing is left to plot.
 */
export function chartData(
  chart: Chart,
  tables: Table[],
  language: "tr" | "en",
): ChartData | null {
  const table = tables[chart.table];
  if (!table) return null;
  const xIndex = table.columns.indexOf(chart.x_column);
  if (xIndex < 0) return null;
  const labels = table.rows.map((row) => row[xIndex]?.text ?? "");
  const series = chart.series
    .map((name) => {
      const column = table.columns.indexOf(name);
      const points = table.rows.map((row, index) => {
        const cell = column >= 0 ? row[column] : undefined;
        const value = cell ? parseFigure(cell.text, language) : null;
        return cell && value !== null
          ? { row: index, x: labels[index]!, value, text: cell.text }
          : null;
      });
      return { name, points };
    })
    .filter((s) => s.points.some((p) => p !== null));
  return series.length > 0 && labels.length > 0
    ? { kind: chart.kind, xColumn: chart.x_column, labels, series }
    : null;
}

/** Round axis bounds and 3–6 ticks covering [min, max], always including zero. */
export function niceTicks(min: number, max: number): number[] {
  const low = Math.min(0, min);
  const high = Math.max(0, max);
  if (low === high) return [0, 1];
  const raw = (high - low) / 4;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step =
    [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= raw) ?? raw;
  const start = Math.floor(low / step) * step;
  const end = Math.ceil(high / step) * step;
  const ticks: number[] = [];
  for (let v = start; v <= end + step / 2; v += step)
    ticks.push(Math.abs(v) < step / 1e6 ? 0 : Number(v.toPrecision(12)));
  return ticks;
}
