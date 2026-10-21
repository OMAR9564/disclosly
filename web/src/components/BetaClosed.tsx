"use client";

import Link from "next/link";

import { SiteFooter } from "@/components/SiteFooter";
import { FlapRow } from "@/components/FlapRow";
import { LanguageToggle } from "@/components/LanguageToggle";
import { useLanguage } from "@/i18n/useLanguage";

export function BetaClosed() {
  const { t, toggle } = useLanguage();
  return (
    <main className="board">
      <header className="board__top">
        <Link href="/" className="board__sources board__home" lang="en">
          Disclosly
        </Link>
        <LanguageToggle t={t} onToggle={toggle} />
      </header>

      <section className="gate" aria-labelledby="beta-title">
        <h1 id="beta-title" className="gate__name">
          <span className="sr-only">{t.betaTitle}</span>
          <FlapRow text={t.betaTitle} length={11} locale={t.locale} />
        </h1>
        <p className="placard">{t.betaBody}</p>
        <p className="board__next">
          <Link href="/" className="key">
            {t.betaHome}
          </Link>
        </p>
      </section>

      <SiteFooter t={t} />
    </main>
  );
}
