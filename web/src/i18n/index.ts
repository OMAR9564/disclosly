import { en } from "./en";
import { type Dictionary, tr } from "./tr";

export type Language = "tr" | "en";
export type { Dictionary };

export const DEFAULT_LANGUAGE: Language = "tr";

export const dictionaries: Record<Language, Dictionary> = { tr, en };

export function isLanguage(value: unknown): value is Language {
  return value === "tr" || value === "en";
}
