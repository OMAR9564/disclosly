import type { Dictionary } from "@/i18n";

export function Disclaimer({ t }: { t: Dictionary }) {
  return (
    <aside
      data-testid="disclaimer"
      className="plate"
      aria-label={t.disclaimerLabel}
    >
      <p className="plate__title">{t.disclaimer}</p>
      <p className="plate__body">{t.disclaimerBody}</p>
    </aside>
  );
}
