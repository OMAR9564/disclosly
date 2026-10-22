"use client";

import { useEffect, useRef } from "react";

import { type Dictionary, isLanguage } from "@/i18n";
import { docTypeLabel, metricLabel } from "@/i18n/labels";
import { type Citation, isCalculation } from "@/lib/api";
import { sourceCode } from "@/lib/runState";

/** The source plate: an enamel plate that opens in place under the row that cites it. */
export function CitationPopover({
  citation,
  marker,
  t,
  onClose,
}: {
  citation: Citation;
  marker: number | null;
  t: Dictionary;
  onClose: () => void;
}) {
  const ref = useRef<HTMLElement>(null);
  const language = isLanguage(t.locale) ? t.locale : "tr";

  useEffect(() => {
    ref.current?.focus();
  }, [citation.cite_id]);

  return (
    <aside
      ref={ref}
      tabIndex={-1}
      className="source"
      data-testid="citation"
      aria-label={`${t.sourceOpen}${marker ? ` [${marker}]` : ""}`}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <div className="source__head">
        <p className="source__code">
          {marker && <span className="source__marker">[{marker}]</span>}
          {sourceCode(citation)}
        </p>
        <button type="button" className="key key--plate" onClick={onClose}>
          {t.sourceClose}
        </button>
      </div>
      {isCalculation(citation) ? (
        <>
          <dl className="source__meta">
            <div>
              <dt>{t.calcFormula}</dt>
              <dd>{citation.excerpt}</dd>
            </div>
            <div>
              <dt>{t.calcOutput}</dt>
              <dd>{citation.source.output}</dd>
            </div>
          </dl>
          <dl className="source__meta" aria-label={t.calcInputs}>
            {citation.source.inputs.map((input) => (
              <div key={input.name}>
                <dt>{input.name}</dt>
                <dd>{input.value}</dd>
              </div>
            ))}
          </dl>
        </>
      ) : (
        <DocumentDetails citation={citation} t={t} language={language} />
      )}
    </aside>
  );
}

function DocumentDetails({
  citation,
  t,
  language,
}: {
  citation: Citation;
  t: Dictionary;
  language: "tr" | "en";
}) {
  const source = citation.source;
  if (source.kind === "calculation") return null;
  return (
    <>
      <dl className="source__meta">
        <div>
          <dt>{t.sourcePeriod}</dt>
          <dd>{source.period}</dd>
        </div>
        <div>
          <dt>{source.metric ? t.sourceMetric : t.sourceSection}</dt>
          <dd>
            {source.metric
              ? metricLabel(source.metric, language)
              : (source.section ?? "—")}
          </dd>
        </div>
        {source.page ? (
          <div>
            <dt>{t.sourcePage}</dt>
            <dd>{source.page}</dd>
          </div>
        ) : null}
        <div>
          <dt>{t.sourceDocument}</dt>
          <dd>{docTypeLabel(source.doc_type, language)}</dd>
        </div>
      </dl>
      <blockquote className="source__excerpt">{citation.excerpt}</blockquote>
      <a
        className="source__link"
        href={source.source_url}
        target="_blank"
        rel="noopener noreferrer"
      >
        {t.sourceLink}
      </a>
    </>
  );
}
