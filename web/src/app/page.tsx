"use client";

import { useEffect, useState } from "react";

import { BackendStatus } from "@/components/BackendStatus";
import { Disclaimer } from "@/components/Disclaimer";
import { FlapRow } from "@/components/FlapRow";
import { LanguageToggle } from "@/components/LanguageToggle";
import { useLanguage } from "@/i18n/useLanguage";
import { type Health, fetchHealth } from "@/lib/health";

export default function Home() {
  const { t, toggle } = useLanguage();
  const [health, setHealth] = useState<Health>({ state: "loading" });

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

      <BackendStatus health={health} t={t} />

      <Disclaimer t={t} />
    </main>
  );
}
