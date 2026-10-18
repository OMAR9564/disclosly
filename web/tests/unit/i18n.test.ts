import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_LANGUAGE, dictionaries } from "@/i18n";
import { en } from "@/i18n/en";
import { tr } from "@/i18n/tr";
import { STORAGE_KEY, useLanguage } from "@/i18n/useLanguage";

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("dictionaries", () => {
  it("have identical keys", () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(tr).sort());
  });

  it("use the fixed disclaimer and toggle texts", () => {
    expect(tr.disclaimer).toBe("Yatırım tavsiyesi değildir");
    expect(en.disclaimer).toBe("Not investment advice");
    expect(tr.toggleLabel).toBe("English");
    expect(en.toggleLabel).toBe("Türkçe");
    expect(dictionaries.tr).toBe(tr);
  });

  it("default to Turkish", () => {
    expect(DEFAULT_LANGUAGE).toBe("tr");
  });
});

describe("useLanguage", () => {
  it("starts in Turkish with empty storage", () => {
    const { result } = renderHook(() => useLanguage());
    expect(result.current.language).toBe("tr");
  });

  it("reads and persists the choice", () => {
    window.localStorage.setItem(STORAGE_KEY, "en");
    const { result } = renderHook(() => useLanguage());
    expect(result.current.language).toBe("en");

    act(() => result.current.toggle());
    expect(result.current.language).toBe("tr");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("tr");
  });

  it("ignores unknown stored values", () => {
    window.localStorage.setItem(STORAGE_KEY, "de");
    const { result } = renderHook(() => useLanguage());
    expect(result.current.language).toBe("tr");
  });

  it("falls back to Turkish when storage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const { result } = renderHook(() => useLanguage());
    expect(result.current.language).toBe("tr");

    act(() => result.current.toggle());
    expect(result.current.language).toBe("en");
  });
});
