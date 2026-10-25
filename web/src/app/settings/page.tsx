"use client";

import Link from "next/link";
import { useEffect, useId, useState } from "react";

import { FlapRow } from "@/components/FlapRow";
import { LanguageToggle } from "@/components/LanguageToggle";
import { SiteFooter } from "@/components/SiteFooter";
import { WatchlistEditor } from "@/components/WatchlistEditor";
import type { Dictionary, Language } from "@/i18n";
import { useLanguage } from "@/i18n/useLanguage";
import {
  ApiError,
  type Memory,
  PERIOD_RANGES,
  type PeriodRange,
  type Preferences,
  deleteMemory,
  getMemory,
  savePreferences,
} from "@/lib/api";
import { errorMessage } from "@/lib/messages";

/**
 * Watchlist and settings (FR-033): the user's memory set on the board. Three bands under the
 * name row: the watchlist with its search, the preferences, and deleting everything. Every
 * change is saved through the API at once and applies from the next question.
 */

const EMPTY: Memory = {
  watchlist: [],
  preferences: { answer_language: null, default_period_range: null },
  limits: { watchlist_max: 50 },
};

function rangeLabel(range: PeriodRange, t: Dictionary): string {
  return t[`range_${range}`];
}

function PreferencesForm({
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
  const id = useId();
  const [draft, setDraft] = useState<Preferences>(memory.preferences);
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- follow saved values (delete all)
    setDraft(memory.preferences);
  }, [memory.preferences]);

  const save = async () => {
    setState("saving");
    setError(null);
    try {
      onChange(await savePreferences(draft, language));
      setState("saved");
    } catch (caught) {
      setError(
        errorMessage(
          caught instanceof ApiError ? caught.code : "internal_error",
          t,
        ),
      );
      setState("idle");
    }
  };

  return (
    <section className="memory" aria-labelledby={`${id}-head`}>
      <h2 id={`${id}-head`} className="row__label">
        {t.preferencesLabel}
      </h2>
      <form
        className="memory__prefs"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <div className="plan-step__pair">
          <div className="plan-field">
            <label htmlFor={`${id}-lang`} className="plan-field__label">
              {t.prefLanguage}
            </label>
            <select
              id={`${id}-lang`}
              className="plan-field__input"
              value={draft.answer_language ?? ""}
              aria-describedby={`${id}-lang-hint`}
              onChange={(event) => {
                setState("idle");
                setDraft({
                  ...draft,
                  answer_language: (event.target.value ||
                    null) as Language | null,
                });
              }}
            >
              <option value="">{t.prefLanguageAuto}</option>
              <option value="tr">{t.prefLanguageTr}</option>
              <option value="en">{t.prefLanguageEn}</option>
            </select>
            <p id={`${id}-lang-hint`} className="memory__hint">
              {t.prefLanguageHint}
            </p>
          </div>
          <div className="plan-field">
            <label htmlFor={`${id}-range`} className="plan-field__label">
              {t.prefPeriod}
            </label>
            <select
              id={`${id}-range`}
              className="plan-field__input"
              value={draft.default_period_range ?? "last_4q"}
              aria-describedby={`${id}-range-hint`}
              onChange={(event) => {
                setState("idle");
                setDraft({
                  ...draft,
                  default_period_range: event.target.value as PeriodRange,
                });
              }}
            >
              {PERIOD_RANGES.map((range) => (
                <option key={range} value={range}>
                  {rangeLabel(range, t)}
                </option>
              ))}
            </select>
            <p id={`${id}-range-hint`} className="memory__hint">
              {t.prefPeriodHint}
            </p>
          </div>
        </div>
        <div className="plan__keys">
          <button
            type="submit"
            className="key key--primary"
            disabled={state === "saving"}
          >
            {state === "saving" ? t.prefSaving : t.prefSave}
          </button>
          <p className="memory__status" role="status">
            {state === "saved" ? t.prefSaved : ""}
          </p>
        </div>
        {error ? <p className="plan-step__reason">{error}</p> : null}
      </form>
    </section>
  );
}

function Forget({
  memory,
  onDeleted,
  language,
  t,
}: {
  memory: Memory;
  onDeleted: () => void;
  language: Language;
  t: Dictionary;
}) {
  const id = useId();
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await deleteMemory(language);
      onDeleted();
      setAsking(false);
      setDone(true);
    } catch (caught) {
      setError(
        errorMessage(
          caught instanceof ApiError ? caught.code : "internal_error",
          t,
        ),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="memory memory--forget" aria-labelledby={`${id}-head`}>
      <h2 id={`${id}-head`} className="row__label">
        {t.forgetLabel}
      </h2>
      <p className="row__note">{t.forgetBody}</p>
      {asking ? (
        <div
          className="memory__confirm"
          role="group"
          aria-labelledby={`${id}-ask`}
        >
          <p id={`${id}-ask`} className="memory__ask">
            {t.forgetAsk.replace("{count}", String(memory.watchlist.length))}
          </p>
          <div className="plan__keys">
            <button
              type="button"
              className="key key--primary"
              disabled={busy}
              onClick={() => void confirm()}
            >
              {t.forgetConfirm}
            </button>
            <button
              type="button"
              className="key key--quiet"
              disabled={busy}
              onClick={() => setAsking(false)}
            >
              {t.forgetCancel}
            </button>
          </div>
        </div>
      ) : (
        <div className="plan__keys">
          <button
            type="button"
            className="key"
            onClick={() => {
              setDone(false);
              setAsking(true);
            }}
          >
            {t.forgetStart}
          </button>
          <p className="memory__status" role="status">
            {done ? t.forgetDone : ""}
          </p>
        </div>
      )}
      {error ? <p className="plan-step__reason">{error}</p> : null}
    </section>
  );
}

export default function SettingsPage() {
  const { language, t, toggle } = useLanguage();
  const [memory, setMemory] = useState<Memory | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getMemory(language).then(
      (loaded) => {
        if (active) setMemory(loaded);
      },
      (caught: unknown) => {
        if (active)
          setLoadError(
            errorMessage(
              caught instanceof ApiError ? caught.code : "internal_error",
              t,
            ),
          );
      },
    );
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load once; language only sets message language
  }, []);

  return (
    <main className="board">
      <header className="board__top">
        <Link href="/" className="board__sources board__home" lang="en">
          Disclosly
        </Link>
        <LanguageToggle t={t} onToggle={toggle} />
      </header>

      <div className="board__name">
        <h1 className="name">
          <span className="sr-only">{t.settingsTitle}</span>
          <FlapRow text={t.watchlistLabel} length={13} locale={t.locale} />
        </h1>
        <p className="placard">{t.settingsIntro}</p>
      </div>

      {memory === null ? (
        <p className="row__note" role="status">
          {loadError ?? t.memoryLoading}
        </p>
      ) : (
        <div className="memory-bands">
          <WatchlistEditor
            memory={memory}
            onChange={setMemory}
            language={language}
            t={t}
          />
          <PreferencesForm
            memory={memory}
            onChange={setMemory}
            language={language}
            t={t}
          />
          <Forget
            memory={memory}
            onDeleted={() => setMemory({ ...EMPTY, limits: memory.limits })}
            language={language}
            t={t}
          />
        </div>
      )}

      <SiteFooter t={t} />
    </main>
  );
}
