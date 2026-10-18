import type { Dictionary } from "@/i18n";

export function LanguageToggle({
  t,
  onToggle,
}: {
  t: Dictionary;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      data-testid="language-toggle"
      className="toggle"
      onClick={onToggle}
      lang={t.toggleLabel === "English" ? "en" : "tr"}
    >
      {t.toggleLabel}
    </button>
  );
}
