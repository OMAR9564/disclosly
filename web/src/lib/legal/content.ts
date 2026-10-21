import { readFileSync } from "node:fs";
import { join } from "node:path";

import type { Language } from "@/i18n";

export type LegalDoc = "privacy" | "terms" | "disclaimer";
export type LegalContent = Record<Language, string>;

const DIR = join(process.cwd(), "src", "content", "legal");

/** Read at build time on the server; both languages ship so the toggle needs no request. */
export function loadLegal(doc: LegalDoc): LegalContent {
  const read = (language: Language) =>
    readFileSync(join(DIR, `${doc}.${language}.md`), "utf8");
  return { tr: read("tr"), en: read("en") };
}
