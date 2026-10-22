"use client";

import { type KeyboardEvent, useEffect, useRef, useState } from "react";

import { FlapRow } from "@/components/FlapRow";
import type { Dictionary } from "@/i18n";
import type { Chart, Table } from "@/lib/api";
import { type ChartData, chartData, niceTicks } from "@/lib/figures";

/**
 * A hand-written SVG line or bar chart over one report table (research §R12). Values are read
 * from the table's cells; the readout above the plot shows the cells' own text, and a hidden
 * table carries the same values for screen readers.
 */

const HEIGHT = 260;
const PAD = { top: 12, right: 12, bottom: 34, left: 56 };
const READOUT_TILES = 8;
// Series are told apart by stroke pattern and marker shape, never by a new color.
const DASHES = ["none", "7 5", "2 4", "10 4 2 4"];
const FILLS = [
  "var(--letter)",
  "var(--letter-muted)",
  "url(#hatch)",
  "var(--flap-hover)",
];

function useWidth(fallback: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.max(240, Math.round(entry.contentRect.width)));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
}

function Marker({ index, x, y }: { index: number; x: number; y: number }) {
  const s = 4.5;
  switch (index % 4) {
    case 1:
      return <circle cx={x} cy={y} r={s} />;
    case 2:
      return (
        <path
          d={`M${x} ${y - s - 1}L${x + s + 1} ${y}L${x} ${y + s + 1}L${x - s - 1} ${y}Z`}
        />
      );
    case 3:
      return (
        <path
          d={`M${x} ${y - s - 1}L${x + s + 1} ${y + s}L${x - s - 1} ${y + s}Z`}
        />
      );
    default:
      return <rect x={x - s} y={y - s} width={s * 2} height={s * 2} rx={1} />;
  }
}

function Plot({
  data,
  width,
  focus,
  locale,
}: {
  data: ChartData;
  width: number;
  focus: number | null;
  locale: string;
}) {
  const values = data.series.flatMap((s) =>
    s.points.flatMap((p) => (p ? [p.value] : [])),
  );
  const ticks = niceTicks(Math.min(...values), Math.max(...values));
  const low = ticks[0]!;
  const high = ticks[ticks.length - 1]!;
  const plotW = width - PAD.left - PAD.right;
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const n = data.labels.length;
  const band = plotW / n;
  const cx = (i: number) => PAD.left + band * i + band / 2;
  const cy = (v: number) =>
    PAD.top + plotH - ((v - low) / (high - low)) * plotH;
  const compact = new Intl.NumberFormat(locale, {
    notation: "compact",
    maximumFractionDigits: 1,
  });
  // Thin the x labels so they never collide: about one per 64px.
  const every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(plotW / 64))));
  const groupW = Math.min(band * 0.72, 22 * data.series.length + 8);
  const barW = groupW / data.series.length;

  return (
    <svg
      className="chart__svg"
      width={width}
      height={HEIGHT}
      viewBox={`0 0 ${width} ${HEIGHT}`}
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <pattern
          id="hatch"
          width="5"
          height="5"
          patternUnits="userSpaceOnUse"
          patternTransform="rotate(45)"
        >
          <rect width="5" height="5" fill="var(--flap)" />
          <line
            x1="0"
            y1="0"
            x2="0"
            y2="5"
            stroke="var(--letter)"
            strokeWidth="2"
          />
        </pattern>
      </defs>
      <g className="chart__grid">
        {ticks.map((tick) => (
          <g key={tick}>
            <line
              x1={PAD.left}
              x2={width - PAD.right}
              y1={cy(tick)}
              y2={cy(tick)}
              data-zero={tick === 0 || undefined}
            />
            <text x={PAD.left - 8} y={cy(tick)} dy="0.32em" textAnchor="end">
              {compact.format(tick)}
            </text>
          </g>
        ))}
      </g>
      {focus !== null && (
        <rect
          className="chart__focus"
          x={PAD.left + band * focus}
          y={PAD.top}
          width={band}
          height={plotH}
        />
      )}
      <g className="chart__x">
        {data.labels.map((label, i) =>
          i % every === 0 || i === focus ? (
            <text
              key={i}
              x={cx(i)}
              y={HEIGHT - PAD.bottom + 20}
              textAnchor="middle"
              data-focus={i === focus || undefined}
            >
              {label}
            </text>
          ) : null,
        )}
      </g>
      {data.series.map((series, s) =>
        data.kind === "bar" ? (
          <g
            key={series.name}
            className="chart__bars"
            data-series={series.name}
          >
            {series.points.map((p, i) =>
              p ? (
                <rect
                  key={i}
                  x={cx(i) - groupW / 2 + barW * s + 1}
                  y={Math.min(cy(p.value), cy(0))}
                  width={Math.max(1, barW - 2)}
                  height={Math.max(1, Math.abs(cy(p.value) - cy(0)))}
                  fill={FILLS[s % FILLS.length]}
                  data-value={p.value}
                  data-x={p.x}
                />
              ) : null,
            )}
          </g>
        ) : (
          <g
            key={series.name}
            className="chart__line"
            data-series={series.name}
            data-ink={s % 2 ? "muted" : undefined}
          >
            <path
              d={series.points
                .map((p, i) => (p ? `${cx(i)} ${cy(p.value)}` : null))
                .reduce<string>(
                  (d, xy, i, all) =>
                    xy === null
                      ? d
                      : `${d}${i === 0 || all[i - 1] === null ? "M" : "L"}${xy}`,
                  "",
                )}
              strokeDasharray={DASHES[s % DASHES.length]}
            />
            {series.points.map((p, i) =>
              p ? (
                <g
                  key={i}
                  data-value={p.value}
                  data-x={p.x}
                  data-focus={i === focus || undefined}
                >
                  <Marker index={s} x={cx(i)} y={cy(p.value)} />
                </g>
              ) : null,
            )}
          </g>
        ),
      )}
    </svg>
  );
}

function LegendKey({ index, kind }: { index: number; kind: Chart["kind"] }) {
  return (
    <svg
      width="28"
      height="12"
      viewBox="0 0 28 12"
      aria-hidden="true"
      focusable="false"
    >
      {kind === "bar" ? (
        <rect
          x="8"
          y="1"
          width="12"
          height="10"
          rx="1"
          fill={FILLS[index % FILLS.length]}
        />
      ) : (
        <g className="chart__line" data-ink={index % 2 ? "muted" : undefined}>
          <path d="M1 6H27" strokeDasharray={DASHES[index % DASHES.length]} />
          <Marker index={index} x={14} y={6} />
        </g>
      )}
    </svg>
  );
}

export function ReportChart({
  chart,
  tables,
  t,
}: {
  chart: Chart;
  tables: Table[];
  t: Dictionary;
}) {
  const language = t.locale === "en" ? "en" : "tr";
  const data = chartData(chart, tables, language);
  const { ref, width } = useWidth(640);
  const [focus, setFocus] = useState<number | null>(null);
  if (!data) return null;

  const n = data.labels.length;
  const shown = focus ?? n - 1;
  const title = t.chartOf
    .replace("{series}", data.series.map((s) => s.name).join(", "))
    .replace("{x}", data.xColumn);

  const pick = (clientX: number, box: DOMRect) => {
    const plotW = box.width - PAD.left - PAD.right;
    const index = Math.floor(((clientX - box.left - PAD.left) / plotW) * n);
    setFocus(Math.min(n - 1, Math.max(0, index)));
  };

  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const moves: Record<string, number> = {
      ArrowRight: 1,
      ArrowDown: 1,
      ArrowLeft: -1,
      ArrowUp: -1,
    };
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      setFocus(event.key === "Home" ? 0 : n - 1);
    } else if (event.key in moves) {
      event.preventDefault();
      setFocus(Math.min(n - 1, Math.max(0, shown + moves[event.key]!)));
    }
  };

  return (
    <figure className="chart" data-testid="report-chart" data-kind={data.kind}>
      <figcaption className="chart__head">
        <span className="row__label">{t.chartLabel}</span>
        <span className="chart__title">{title}</span>
      </figcaption>

      <div className="chart__readout">
        <span className="chart__at">
          <FlapRow
            text={data.labels[shown] ?? ""}
            length={READOUT_TILES}
            locale={t.locale}
          />
        </span>
        <dl
          className="chart__values"
          aria-hidden="true"
          data-testid="chart-readout"
        >
          {data.series.map((series, s) => (
            <div key={series.name} className="chart__value">
              <dt>
                <LegendKey index={s} kind={data.kind} />
                {series.name}
              </dt>
              <dd>{series.points[shown]?.text ?? "—"}</dd>
            </div>
          ))}
        </dl>
      </div>

      <div
        ref={ref}
        className="chart__plot"
        role="slider"
        tabIndex={0}
        aria-label={`${title}. ${t.chartHint}`}
        aria-valuemin={1}
        aria-valuemax={n}
        aria-valuenow={shown + 1}
        aria-valuetext={[
          data.labels[shown],
          ...data.series.map(
            (s) => `${s.name}: ${s.points[shown]?.text ?? "—"}`,
          ),
        ].join("; ")}
        onKeyDown={onKey}
        onPointerMove={(e) =>
          pick(e.clientX, e.currentTarget.getBoundingClientRect())
        }
        onPointerDown={(e) =>
          pick(e.clientX, e.currentTarget.getBoundingClientRect())
        }
        onPointerLeave={() => setFocus(null)}
        onBlur={() => setFocus(null)}
      >
        <Plot data={data} width={width} focus={focus} locale={t.locale} />
      </div>

      <table className="sr-only">
        <caption>{title}</caption>
        <thead>
          <tr>
            <th scope="col">{data.xColumn}</th>
            {data.series.map((s) => (
              <th key={s.name} scope="col">
                {s.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.labels.map((label, i) => (
            <tr key={i}>
              <th scope="row">{label}</th>
              {data.series.map((s) => (
                <td key={s.name}>{s.points[i]?.text ?? "—"}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
