"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { AskBox } from "@/components/AskBox";
import { BackendStatus } from "@/components/BackendStatus";
import { SiteFooter } from "@/components/SiteFooter";
import { FlapRow } from "@/components/FlapRow";
import { LanguageToggle } from "@/components/LanguageToggle";
import { QuotaBadge, quotaExhaustedMessage } from "@/components/QuotaBadge";
import { useLanguage } from "@/i18n/useLanguage";
import { ApiError, type Quotas, getMe, startResearch } from "@/lib/api";
import { type Health, fetchHealth } from "@/lib/health";
import { errorMessage } from "@/lib/messages";

export default function Home() {
  const { language, t, toggle } = useLanguage();
  const router = useRouter();
  const [health, setHealth] = useState<Health>({ state: "loading" });
  const [busy, setBusy] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const [quota, setQuota] = useState<Quotas | null>(null);
  // Bumped after a question is sent: the page stays mounted while hidden, so a new key gives
  // the visitor an empty box when they come back for the next question.
  const [askKey, setAskKey] = useState(0);
  const exhausted = quota !== null && quota.simple.remaining <= 0;

  const ask = async (question: string) => {
    setBusy(true);
    setAskError(null);
    try {
      const { run_id, quota: next } = await startResearch(question, language);
      if (next) setQuota(next);
      setAskKey((key) => key + 1);
      router.push(`/runs/${encodeURIComponent(run_id)}`);
    } catch (error) {
      const code = error instanceof ApiError ? error.code : "internal_error";
      if (code === "quota_exhausted" && quota)
        setQuota({ ...quota, simple: { ...quota.simple, remaining: 0 } });
      setAskError(code);
      setBusy(false);
    }
  };

  // Next keeps a left page alive in a hidden <Activity> and replays its effects when the visitor
  // comes back ("Yeni soru"), so the ask key must not stay in its sending state.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset on every reveal, see above
    setBusy(false);
  }, []);

  useEffect(() => {
    let active = true;
    void fetchHealth().then((result) => {
      if (active) setHealth(result);
    });
    // Signed-out visitors simply get no badge.
    getMe(language).then(
      (me) => {
        if (active && typeof me?.quota?.simple?.remaining === "number")
          setQuota(me.quota);
      },
      () => undefined,
    );
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load once; language only sets message language
  }, []);

  return (
    <main className="board">
      <header className="board__top">
        <p className="board__sources">{t.sourcesValue}</p>
        <div className="board__keys">
          {quota ? <QuotaBadge quota={quota} t={t} /> : null}
          {quota ? (
            <Link href="/settings" className="key key--quiet board__link">
              {t.settingsLink}
            </Link>
          ) : null}
          <LanguageToggle t={t} onToggle={toggle} />
        </div>
      </header>

      <div className="board__name">
        <h1 data-testid="product-name" className="name">
          <span className="sr-only">Disclosly</span>
          <FlapRow text="Disclosly" length={9} />
        </h1>
        <p data-testid="tagline" className="placard">
          {t.tagline}
        </p>
      </div>

      <AskBox
        key={askKey}
        t={t}
        onAsk={(question) => void ask(question)}
        busy={busy}
        disabled={exhausted}
        error={
          exhausted && quota
            ? quotaExhaustedMessage(quota.simple, t)
            : askError === "validation_error"
              ? t.askTooShort
              : askError
                ? errorMessage(askError, t)
                : null
        }
      />

      <BackendStatus health={health} t={t} />

      <SiteFooter t={t} />
    </main>
  );
}
