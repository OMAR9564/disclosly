"use client";

import { useCallback, useEffect, useState } from "react";

import {
  DEFAULT_LANGUAGE,
  type Dictionary,
  type Language,
  dictionaries,
  isLanguage,
} from ".";
import { updateLanguage } from "@/lib/api";

export const STORAGE_KEY = "disclosly.language";

function readStoredLanguage(): Language {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return isLanguage(stored) ? stored : DEFAULT_LANGUAGE;
  } catch {
    return DEFAULT_LANGUAGE;
  }
}

function storeLanguage(language: Language): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // Storage blocked (private mode, policy): the choice lasts for this page view only.
  }
}

/** Signed in, the choice also follows the account (PUT /v1/me); signed out, the 401 is ignored. */
export function syncLanguage(
  language: Language,
  save: (language: Language) => Promise<unknown> = updateLanguage,
): void {
  save(language).catch(() => undefined);
}

export function useLanguage(): {
  language: Language;
  t: Dictionary;
  toggle: () => void;
} {
  // Start from the default so server and first client render match; read storage after mount.
  const [language, setLanguage] = useState<Language>(DEFAULT_LANGUAGE);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- storage is client-only; reading it during render would break hydration
    setLanguage(readStoredLanguage());
  }, []);

  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);

  const toggle = useCallback(() => {
    const next: Language = language === "tr" ? "en" : "tr";
    storeLanguage(next);
    setLanguage(next);
    syncLanguage(next);
  }, [language]);

  return { language, t: dictionaries[language], toggle };
}
