import type { ReactNode } from "react";

import type { Dictionary } from "@/i18n";
import type { Table } from "@/lib/api";
import { parseFigure } from "@/lib/figures";

/**
 * One report table as a quote board: label-style column heads, figures right-aligned in
 * tabular numerals, and a marker key on every cited cell that opens its source plate under the
 * table. Wide tables scroll inside their own frame; the page never scrolls sideways.
 */
export function ReportTable({
  table,
  index,
  markers,
  open,
  onCite,
  plate,
  t,
}: {
  table: Table;
  index: number;
  /** Marker number per cite id: the report's own markers, then one per further cited cell. */
  markers: Map<string, number>;
  /** The cite id whose plate is open under this table, if any. */
  open: string | null;
  onCite: (citeId: string) => void;
  plate: ReactNode;
  t: Dictionary;
}) {
  const language = t.locale === "en" ? "en" : "tr";
  const numeric = table.columns.map(
    (_, c) =>
      c > 0 &&
      table.rows.some(
        (row) => row[c] && parseFigure(row[c].text, language) !== null,
      ),
  );
  const titleId = `table-${index}-title`;

  return (
    <section
      className="report-table"
      data-testid="report-table"
      aria-labelledby={titleId}
    >
      <h3 id={titleId} className="report-table__title">
        {table.title}
      </h3>
      <div
        className="report-table__frame"
        role="region"
        tabIndex={0}
        aria-label={`${table.title}. ${t.tableScroll}`}
      >
        <table className="report-table__grid">
          <thead>
            <tr>
              {table.columns.map((column, c) => (
                <th key={c} scope="col" data-numeric={numeric[c] || undefined}>
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, r) => (
              <tr key={r}>
                {table.columns.map((_, c) => {
                  const cell = row[c];
                  const Tag = c === 0 ? "th" : "td";
                  const citeId = cell?.cite_id ?? null;
                  const marker = citeId ? markers.get(citeId) : undefined;
                  return (
                    <Tag
                      key={c}
                      scope={c === 0 ? "row" : undefined}
                      data-numeric={numeric[c] || undefined}
                    >
                      {cell?.text ?? ""}
                      {citeId && marker !== undefined && (
                        <button
                          type="button"
                          className="marker"
                          aria-expanded={open === citeId}
                          aria-label={`${t.sourceOpen} ${marker}: ${cell?.text ?? ""}`}
                          onClick={() => onCite(citeId)}
                        >
                          {marker}
                        </button>
                      )}
                    </Tag>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {plate}
    </section>
  );
}
