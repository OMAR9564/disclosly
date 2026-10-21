import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { STORAGE_KEY, syncLanguage, useLanguage } from "@/i18n/useLanguage";
import { updateLanguage } from "@/lib/api";

vi.mock("@/lib/api", () => ({ updateLanguage: vi.fn() }));
const save = vi.mocked(updateLanguage);

beforeEach(() => {
  window.localStorage.clear();
  save.mockReset();
  save.mockResolvedValue(undefined as never);
});

afterEach(() => vi.restoreAllMocks());

describe("useLanguage", () => {
  it("starts in Turkish and reads a stored choice after mount", () => {
    window.localStorage.setItem(STORAGE_KEY, "en");
    const { result } = renderHook(() => useLanguage());
    expect(result.current.language).toBe("en");
    expect(document.documentElement.lang).toBe("en");
    expect(save).not.toHaveBeenCalled();
  });

  it("stores the toggle locally and saves it on the account", () => {
    const { result } = renderHook(() => useLanguage());
    act(() => result.current.toggle());
    expect(result.current.language).toBe("en");
    expect(result.current.t.locale).toBe("en");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("en");
    expect(save).toHaveBeenLastCalledWith("en");

    act(() => result.current.toggle());
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("tr");
    expect(save).toHaveBeenLastCalledWith("tr");
  });

  it("keeps working when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const { result } = renderHook(() => useLanguage());
    act(() => result.current.toggle());
    expect(result.current.language).toBe("en");
  });
});

describe("syncLanguage", () => {
  it("ignores a signed-out 401", async () => {
    const failing = vi.fn().mockRejectedValue(new Error("unauthorized"));
    expect(() => syncLanguage("en", failing)).not.toThrow();
    await Promise.resolve();
    expect(failing).toHaveBeenCalledWith("en");
  });
});
