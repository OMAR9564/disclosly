import Link from "next/link";

import { Disclaimer } from "@/components/Disclaimer";
import type { Dictionary } from "@/i18n";

/** Legal links and the disclaimer plate, at the foot of every page. */
export function SiteFooter({ t }: { t: Dictionary }) {
  return (
    <footer className="board__foot">
      <nav className="legal-links" aria-label={t.legalNav}>
        <Link href="/privacy">{t.privacyLink}</Link>
        <span aria-hidden="true">·</span>
        <Link href="/terms">{t.termsLink}</Link>
        <span aria-hidden="true">·</span>
        <Link href="/disclaimer">{t.disclaimerLink}</Link>
      </nav>
      <Disclaimer t={t} />
    </footer>
  );
}
