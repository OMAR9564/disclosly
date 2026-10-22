"use client";

import { useState } from "react";

import type { Dictionary } from "@/i18n";
import { metricLabel } from "@/i18n/labels";
import type { CalculationSource, Citation } from "@/lib/api";
import { sourceCode } from "@/lib/runState";

/**
 * The body of a source plate for a `k:` calculation (contracts/report.md): the formula in
 * words, the result, each input with its value and its own source (opened in place), and the
 * code behind a toggle. The plate itself (frame, head, close key) is CitationPopover's.
 */

export function formatDecimal(value: string, locale: string): string {
  const number = Number(value);
  if (!Number.isFinite(number)) return value;
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 6 }).format(
    number,
  );
}

export function CalculationPopover({
  citation,
  source,
  byId,
  t,
}: {
  citation: Citation;
  source: CalculationSource;
  byId: ReadonlyMap<string, Citation>;
  t: Dictionary;
}) {
  const [opened, setOpened] = useState<string | null>(null);
  const language = t.locale === "en" ? "en" : "tr";

  return (
    <div className="calc" data-testid="calculation">
      <p className="calc__formula">
        <span className="calc__label">{t.calcFormula}</span>
        {citation.excerpt}
      </p>
      <p className="calc__result">
        <span className="calc__label">{t.calcOutput}</span>
        <span className="calc__value" data-testid="calculation-output">
          {formatDecimal(source.output, t.locale)}
        </span>
      </p>

      <h4 className="calc__label calc__inputs-title">{t.calcInputs}</h4>
      <ul className="calc__inputs">
        {source.inputs.map((input) => {
          const cited = byId.get(input.cite_id);
          const open = opened === input.cite_id;
          const panel = `calc-${citation.cite_id}-${input.name}`;
          return (
            <li key={input.name} className="calc__input">
              <span className="calc__name">{input.name}</span>
              <span className="calc__amount">
                {formatDecimal(input.value, t.locale)}
              </span>
              {cited && (
                <button
                  type="button"
                  className="calc__source"
                  aria-expanded={open}
                  aria-controls={panel}
                  onClick={() => setOpened(open ? null : input.cite_id)}
                >
                  {sourceCode(cited)}
                </button>
              )}
              {cited && open && (
                <div
                  id={panel}
                  className="calc__cited"
                  data-testid="calculation-input-source"
                >
                  {cited.source.kind !== "calculation" &&
                    cited.source.metric && (
                      <p className="calc__metric">
                        {metricLabel(cited.source.metric, language)} ·{" "}
                        {cited.source.period}
                      </p>
                    )}
                  <blockquote className="source__excerpt">
                    {cited.excerpt}
                  </blockquote>
                  {cited.source.kind !== "calculation" && (
                    <a
                      className="source__link"
                      href={cited.source.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {t.sourceLink}
                    </a>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>

      <details className="calc__code">
        <summary className="calc__toggle">{t.calcShowCode}</summary>
        <pre>
          <code>{source.code}</code>
        </pre>
      </details>
    </div>
  );
}
