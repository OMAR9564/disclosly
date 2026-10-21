"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useLanguage } from "@/i18n/useLanguage";
import { errorMessage } from "@/lib/messages";
import { useRun } from "@/lib/useRun";

import { AnswerView } from "./AnswerView";
import { SiteFooter } from "./SiteFooter";
import { FlapRow } from "./FlapRow";
import { LanguageToggle } from "./LanguageToggle";
import { Timeline } from "./Timeline";

/** The run id from `/runs/<id>`. Read from the URL, not `useParams`: on Vercel the cached
 * prerender shell can hand the client a placeholder param, and the API then answers 422. */
export function runIdFromPath(pathname: string | null): string {
  const last = (pathname ?? "").split("/").filter(Boolean).pop() ?? "";
  try {
    return decodeURIComponent(last);
  } catch {
    return last;
  }
}

/** Reload-safe view of one run: the stream replays its history, then the stored answer. */
export function RunView() {
  const id = runIdFromPath(usePathname());
  const { language, t, toggle } = useLanguage();
  const { run, state, lost, loadError } = useRun(id, language);
  const failure =
    loadError ?? (state.phase === "error" ? state.errorCode : null);

  return (
    <main className="board">
      <header className="board__top">
        <Link href="/" className="board__sources board__home">
          Disclosly
        </Link>
        <LanguageToggle t={t} onToggle={toggle} />
      </header>

      <div className="board__name">
        <h1 className="sr-only">{run?.question ?? t.answerLabel}</h1>
        <p className="placard question" data-testid="question">
          {run?.question ?? " "}
        </p>
      </div>

      {!loadError && <Timeline state={state} t={t} />}

      {failure && (
        <section className="row" role="alert" data-testid="run-error">
          <div className="row__board row__board--single">
            <FlapRow text={t.boardError} length={7} locale={t.locale} />
          </div>
          <p className="row__note">{errorMessage(failure, t)}</p>
        </section>
      )}

      {lost && state.phase !== "done" && (
        <p className="row__note" role="alert">
          {t.runLost}
        </p>
      )}

      {state.answer && (
        <AnswerView answer={state.answer} citations={state.citations} t={t} />
      )}

      <p className="board__next">
        <Link href="/" className="key">
          {t.newQuestion}
        </Link>
      </p>

      <SiteFooter t={t} />
    </main>
  );
}
