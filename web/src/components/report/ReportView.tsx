"use client";

import { Fragment, useState } from "react";

import { CitationPopover } from "@/components/CitationPopover";
import { FlapRow } from "@/components/FlapRow";
import type { Dictionary } from "@/i18n";
import { type Citation, type Report, asReport } from "@/lib/api";
import { leadFigure, sourceCode } from "@/lib/runState";

import { ReportChart } from "./ReportChart";
import { ReportTable } from "./ReportTable";

const FIGURE_TILES = 9;
const CONFIDENCE_TILES = 6;

type Open = { citeId: string; marker: number | null; where: string } | null;

function banner(report: Report, t: Dictionary): string | null {
  return (
    {
      completed: null,
      partial: t.bannerPartial,
      refused: t.bannerRefused,
      no_evidence: t.bannerNoEvidence,
    }[report.status] ?? null
  );
}

/** The API's bilingual disclaimer, "TR / EN", shown in the board's language. */
function localDisclaimer(text: string, t: Dictionary): string {
  const [first, second] = text.split(" / ");
  return t.locale === "en" && second ? second : (first ?? text);
}

/** The report's own markers, then the next numbers for cited table cells it did not list. */
function markerMap(report: Required<Report>): Map<string, number> {
  const markers = new Map(report.citations.map((c) => [c.cite_id, c.marker]));
  let next = Math.max(0, ...markers.values());
  for (const table of report.tables)
    for (const row of table.rows)
      for (const cell of row)
        if (cell.cite_id && !markers.has(cell.cite_id))
          markers.set(cell.cite_id, ++next);
  return markers;
}

/**
 * The final report on the board (contracts/report.md): banner, summary placard with [n] keys,
 * confidence, arrival rows of figures, the chart and tables, what could not be verified, and
 * the disclaimer. Phase 3 answers render through the same view with the new parts empty.
 */
export function ReportView({
  answer,
  citations,
  t,
}: {
  answer: Report;
  citations: Citation[];
  t: Dictionary;
}) {
  const report = asReport(answer);
  const [open, setOpen] = useState<Open>(null);
  const byId = new Map(citations.map((c) => [c.cite_id, c]));
  const markers = markerMap(report);
  const note = banner(report, t);

  const toggle = (citeId: string, marker: number | null, where: string) =>
    setOpen((current) =>
      current?.citeId === citeId && current.where === where
        ? null
        : { citeId, marker, where },
    );

  const plate = (where: string) => {
    if (open?.where !== where) return null;
    const citation = byId.get(open.citeId);
    return citation ? (
      <CitationPopover
        citation={citation}
        marker={open.marker}
        t={t}
        onClose={() => setOpen(null)}
        byId={byId}
      />
    ) : null;
  };

  const summaryParts = report.summary.split(/(\[\d+\])/);
  const confidence = report.confidence;
  const confidenceWord = confidence
    ? {
        high: t.confidenceHigh,
        medium: t.confidenceMedium,
        low: t.confidenceLow,
      }[confidence.level]
    : null;

  return (
    <section
      className="answer"
      aria-labelledby="answer-label"
      data-testid="answer"
    >
      <h2 id="answer-label" className="sr-only">
        {t.answerLabel}
      </h2>

      {note && (
        <p className="answer__banner" data-status={report.status} role="note">
          {note}
        </p>
      )}

      {report.summary && (
        <p className="placard answer__summary" data-testid="answer-summary">
          {summaryParts.map((part, i) => {
            if (/^\[\d+\]$/.test(part)) return null;
            const next = summaryParts[i + 1] ?? "";
            const match = /^\[(\d+)\]$/.exec(next);
            const marker = match ? Number(match[1]) : null;
            const ref = report.citations.find((c) => c.marker === marker);
            if (marker === null || !ref || !byId.has(ref.cite_id))
              return (
                <Fragment key={i}>
                  {part}
                  {match ? next : ""}
                </Fragment>
              );
            const split = part.search(/\S+\s*$/);
            const head = split > 0 ? part.slice(0, split) : "";
            const tail = split >= 0 ? part.slice(split) : part;
            const pressed = open?.where === "summary" && open.marker === marker;
            return (
              <Fragment key={i}>
                {head}
                <span className="cited">
                  {tail}
                  <button
                    type="button"
                    className="marker"
                    aria-expanded={pressed}
                    aria-label={`${t.sourceOpen} ${marker}`}
                    onClick={() => toggle(ref.cite_id, marker, "summary")}
                  >
                    {marker}
                  </button>
                </span>
              </Fragment>
            );
          })}
        </p>
      )}
      {plate("summary")}

      {confidence && confidenceWord && (
        <div
          className="confidence"
          data-level={confidence.level}
          data-testid="report-confidence"
        >
          <p className="row__label" aria-hidden="true">
            {t.confidenceLabel}
          </p>
          <span className="confidence__tiles">
            <FlapRow
              text={confidenceWord}
              length={CONFIDENCE_TILES}
              locale={t.locale}
            />
          </span>
          <p className="confidence__note">
            <span className="sr-only">
              {t.confidenceLabel}: {confidenceWord}.{" "}
            </span>
            {confidence.note}
          </p>
        </div>
      )}

      {report.points.length > 0 && (
        <ol className="arrivals" aria-label={t.figuresLabel}>
          {report.points.map((point, index) => {
            const figure = leadFigure(point.text);
            const citeId = point.cite_ids.find((c) => byId.has(c));
            const citation = citeId ? byId.get(citeId) : undefined;
            const where = `point-${index}`;
            const expanded = open?.where === where;
            return (
              <li key={where} className="arrival">
                <button
                  type="button"
                  className="arrival__row"
                  aria-expanded={citation ? expanded : undefined}
                  disabled={!citation}
                  onClick={() =>
                    citeId && toggle(citeId, markers.get(citeId) ?? null, where)
                  }
                >
                  <span className="arrival__figure">
                    {figure ? (
                      <FlapRow
                        text={figure}
                        length={FIGURE_TILES}
                        locale={t.locale}
                        align="end"
                      />
                    ) : (
                      <FlapRow text="" length={FIGURE_TILES} />
                    )}
                  </span>
                  <span className="arrival__text">{point.text}</span>
                  {citation && (
                    <span className="arrival__code">
                      {sourceCode(citation)}
                    </span>
                  )}
                </button>
                {plate(where)}
              </li>
            );
          })}
        </ol>
      )}

      {report.chart && (
        <ReportChart chart={report.chart} tables={report.tables} t={t} />
      )}

      {report.tables.length > 0 && (
        <div className="report-tables" aria-label={t.tablesLabel} role="group">
          {report.tables.map((table, index) => {
            const where = `table-${index}`;
            return (
              <ReportTable
                key={where}
                table={table}
                index={index}
                markers={markers}
                open={open?.where === where ? open.citeId : null}
                onCite={(citeId) =>
                  toggle(citeId, markers.get(citeId) ?? null, where)
                }
                plate={plate(where)}
                t={t}
              />
            );
          })}
        </div>
      )}

      {report.unverified.length > 0 && (
        <section
          className="unverified"
          aria-labelledby="unverified-label"
          data-testid="report-unverified"
        >
          <h3 id="unverified-label" className="row__label">
            {t.unverifiedLabel}
          </h3>
          <ul className="unverified__list">
            {report.unverified.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </section>
      )}

      <p className="answer__disclaimer" data-testid="answer-disclaimer">
        {localDisclaimer(report.disclaimer, t)}
      </p>
    </section>
  );
}
