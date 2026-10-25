"use client";

import { useEffect, useId, useState } from "react";

import type { Dictionary, Language } from "@/i18n";
import {
  ApiError,
  type Company,
  type Market,
  type Memory,
  addToWatchlist,
  removeFromWatchlist,
  searchCompanies,
} from "@/lib/api";
import { errorMessage } from "@/lib/messages";

import { FlapRow } from "./FlapRow";

/**
 * The watchlist as a quote board (research §R13, FR-033): one row per company with its ticker
 * plate, name and market, and a key to take it off; under it, a blank flap field that searches
 * the covered companies and adds one. Only corpus companies come back from the search, so the
 * list can never hold a company Disclosly cannot answer about.
 */

const SEARCH_DELAY_MS = 250;

function marketLabel(market: Market, t: Dictionary): string {
  return market === "bist" ? t.marketBist : t.marketUs;
}

function Cross() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M3.5 3.5l7 7M10.5 3.5l-7 7"
        stroke="currentColor"
        strokeWidth="2"
      />
    </svg>
  );
}

function fill(template: string, values: Record<string, string | number>) {
  return template.replace(/\{(\w+)\}/g, (_, key: string) =>
    String(values[key] ?? ""),
  );
}

export function WatchlistEditor({
  memory,
  onChange,
  language,
  t,
}: {
  memory: Memory;
  onChange: (memory: Memory) => void;
  language: Language;
  t: Dictionary;
}) {
  const fieldId = useId();
  const resultsId = useId();
  const errorId = useId();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Company[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const listed = new Set(memory.watchlist.map((item) => item.ticker));
  const max = memory.limits.watchlist_max;
  const full = memory.watchlist.length >= max;

  useEffect(() => {
    const needle = query.trim();
    if (!needle) return;
    let active = true;
    const timer = window.setTimeout(() => {
      searchCompanies(needle, language).then(
        (found) => {
          if (active) setResults(found.items);
        },
        () => {
          if (active) setResults([]);
        },
      );
    }, SEARCH_DELAY_MS);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [query, language]);

  const change = async (ticker: string, add: boolean) => {
    setBusy(ticker);
    setError(null);
    try {
      onChange(
        add
          ? await addToWatchlist(ticker, language)
          : await removeFromWatchlist(ticker, language),
      );
    } catch (caught) {
      const code = caught instanceof ApiError ? caught.code : "internal_error";
      setError(errorMessage(code, t));
    } finally {
      setBusy(null);
    }
  };

  // An empty field shows no results, whatever the last search returned.
  const shown = query.trim() ? results : null;
  const count = String(memory.watchlist.length).padStart(2, "0");

  return (
    <section className="memory" aria-labelledby={`${fieldId}-head`}>
      <div className="memory__head">
        <h2 id={`${fieldId}-head`} className="row__label">
          {t.watchlistLabel}
        </h2>
        <p className="memory__count">
          <span className="sr-only">
            {fill(t.watchlistCount, { count: memory.watchlist.length, max })}
          </span>
          <span className="quota__tiles" aria-hidden="true">
            <FlapRow text={count} length={2} align="end" />
          </span>
          <span className="quota__limit" aria-hidden="true">
            / {max}
          </span>
        </p>
      </div>

      {memory.watchlist.length === 0 ? (
        <p className="row__note">{t.watchlistEmpty}</p>
      ) : (
        <ul className="watchlist" data-testid="watchlist">
          {memory.watchlist.map((item) => (
            <li key={item.ticker} className="watchlist__row">
              <span className="plan-step__ticker">{item.ticker}</span>
              <span className="watchlist__name">{item.name}</span>
              <span className="watchlist__market">
                {marketLabel(item.market, t)}
              </span>
              <button
                type="button"
                className="key key--icon"
                aria-label={fill(t.watchlistRemove, { ticker: item.ticker })}
                disabled={busy !== null}
                onClick={() => void change(item.ticker, false)}
              >
                <Cross />
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="memory__search">
        <label htmlFor={fieldId} className="plan-field__label">
          {t.companySearchLabel}
        </label>
        <input
          id={fieldId}
          type="search"
          className="plan-field__input"
          value={query}
          maxLength={50}
          autoComplete="off"
          spellCheck={false}
          placeholder={t.companySearchPlaceholder}
          aria-controls={resultsId}
          aria-describedby={error || full ? errorId : undefined}
          onChange={(event) => setQuery(event.target.value)}
        />
        {error || full ? (
          <p id={errorId} className="plan-step__reason" role="status">
            {error ?? fill(t.watchlistFull, { max })}
          </p>
        ) : null}
        <div id={resultsId} aria-live="polite">
          {shown === null ? null : shown.length === 0 ? (
            <p className="row__note">{t.companySearchNone}</p>
          ) : (
            <ul
              className="watchlist watchlist--results"
              aria-label={t.companySearchResults}
            >
              {shown.map((company) => {
                const present = listed.has(company.ticker);
                return (
                  <li key={company.ticker} className="watchlist__row">
                    <span className="plan-step__ticker">{company.ticker}</span>
                    <span className="watchlist__name">{company.name}</span>
                    <span className="watchlist__market">
                      {marketLabel(company.market, t)}
                    </span>
                    <button
                      type="button"
                      className="key watchlist__add"
                      aria-label={
                        present
                          ? undefined
                          : fill(t.companyAddLabel, { ticker: company.ticker })
                      }
                      disabled={present || full || busy !== null}
                      onClick={() => void change(company.ticker, true)}
                    >
                      {present ? t.companyAdded : t.companyAdd}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
