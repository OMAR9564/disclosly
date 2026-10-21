"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { AskBox } from "@/components/AskBox";
import { BackendStatus } from "@/components/BackendStatus";
import { Disclaimer } from "@/components/Disclaimer";
import { FlapRow } from "@/components/FlapRow";
import { LanguageToggle } from "@/components/LanguageToggle";
import { useLanguage } from "@/i18n/useLanguage";
import { ApiError, startResearch } from "@/lib/api";
import { type Health, fetchHealth } from "@/lib/health";
import { errorMessage } from "@/lib/messages";

export default function Home() {
  const { language, t, toggle } = useLanguage();
  const router = useRouter();
  const [health, setHealth] = useState<Health>({ state: "loading" });
  const [busy, setBusy] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);

  const ask = async (question: string) => {
    setBusy(true);
    setAskError(null);
    try {
      const { run_id } = await startResearch(question, language);
      router.push(`/runs/${encodeURIComponent(run_id)}`);
    } catch (error) {
      setAskError(error instanceof ApiError ? error.code : "internal_error");
      setBusy(false);
    }
  };

  useEffect(() => {
    let active = true;
    void fetchHealth().then((result) => {
      if (active) setHealth(result);
    });
    return () => {
      active = false;
    };
  }, []);

  return (
    <main className="board">
      <header className="board__top">
        <p className="board__sources">{t.sourcesValue}</p>
        <LanguageToggle t={t} onToggle={toggle} />
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
        t={t}
        onAsk={(question) => void ask(question)}
        busy={busy}
        error={askError ? errorMessage(askError, t) : null}
      />

      <BackendStatus health={health} t={t} />

      <Disclaimer t={t} />
    </main>
  );
}
