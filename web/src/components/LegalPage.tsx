"use client";

import Link from "next/link";

import { LanguageToggle } from "@/components/LanguageToggle";
import { SiteFooter } from "@/components/SiteFooter";
import { useLanguage } from "@/i18n/useLanguage";
import type { LegalContent } from "@/lib/legal/content";
import { Markdown } from "@/lib/markdown";

/** A legal document in the visitor's language; readable signed out. */
export function LegalPage({ content }: { content: LegalContent }) {
  const { language, t, toggle } = useLanguage();
  return (
    <main className="board">
      <header className="board__top">
        <Link href="/" className="board__sources board__home" lang="en">
          Disclosly
        </Link>
        <LanguageToggle t={t} onToggle={toggle} />
      </header>

      <article className="legal" lang={language}>
        <Markdown source={content[language]} />
      </article>

      <SiteFooter t={t} />
    </main>
  );
}
