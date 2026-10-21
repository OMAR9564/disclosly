"use client";

import { useEffect, useRef } from "react";

import { type Dictionary, isLanguage } from "@/i18n";
import { docTypeLabel, metricLabel } from "@/i18n/labels";
import type { Citation } from "@/lib/api";
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
  const { source } = citation;
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
    </aside>
  );
}
