"use client";

import { Fragment, useState } from "react";

import type { Dictionary } from "@/i18n";
import type { Answer, Citation } from "@/lib/api";
import { leadFigure, sourceCode } from "@/lib/runState";

import { CitationPopover } from "./CitationPopover";
import { FlapRow } from "./FlapRow";

const FIGURE_TILES = 9;

type Open = { citeId: string; marker: number | null; where: string } | null;

function banner(answer: Answer, t: Dictionary): string | null {
  return (
    {
      completed: null,
      partial: t.bannerPartial,
      refused: t.bannerRefused,
      no_evidence: t.bannerNoEvidence,
    }[answer.status] ?? null
  );
}

/** The API's bilingual disclaimer, "TR / EN", shown in the board's language. */
function localDisclaimer(text: string, t: Dictionary): string {
  const [first, second] = text.split(" / ");
  return t.locale === "en" && second ? second : (first ?? text);
}

/** Summary placard with [n] keys, arrival rows of figures and their source plates. */
export function AnswerView({
  answer,
  citations,
  t,
}: {
  answer: Answer;
  citations: Citation[];
  t: Dictionary;
}) {
  const [open, setOpen] = useState<Open>(null);
  const byId = new Map(citations.map((c) => [c.cite_id, c]));
  const markerOf = new Map(answer.citations.map((c) => [c.cite_id, c.marker]));
  const note = banner(answer, t);

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
      />
    ) : null;
  };

  const summaryParts = answer.summary.split(/(\[\d+\])/);

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
        <p className="answer__banner" data-status={answer.status} role="note">
          {note}
        </p>
      )}

      {answer.summary && (
        <p className="placard answer__summary" data-testid="answer-summary">
          {summaryParts.map((part, i) => {
            if (/^\[\d+\]$/.test(part)) return null;
            const next = summaryParts[i + 1] ?? "";
            const match = /^\[(\d+)\]$/.exec(next);
            const marker = match ? Number(match[1]) : null;
            const ref = answer.citations.find((c) => c.marker === marker);
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

      {answer.points.length > 0 && (
        <ol className="arrivals" aria-label={t.figuresLabel}>
          {answer.points.map((point, index) => {
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
                    citeId &&
                    toggle(citeId, markerOf.get(citeId) ?? null, where)
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

      <p className="answer__disclaimer" data-testid="answer-disclaimer">
        {localDisclaimer(answer.disclaimer, t)}
      </p>
    </section>
  );
}
