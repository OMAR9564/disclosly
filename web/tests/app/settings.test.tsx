import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SettingsPage from "@/app/settings/page";
import type { Memory } from "@/lib/api";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const SAVED: Memory = {
  watchlist: [
    {
      ticker: "AAPL",
      name: "Apple Inc.",
      market: "us",
      added_at: "2026-10-20T09:00:00Z",
    },
  ],
  preferences: { answer_language: "en", default_period_range: "last_8q" },
  limits: { watchlist_max: 50 },
};

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
    init?.method === "DELETE"
      ? new Response(null, { status: 204 })
      : new Response(JSON.stringify(SAVED), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("settings page", () => {
  it("loads the saved watchlist and preferences", async () => {
    render(<SettingsPage />);
    expect(await screen.findByText("Apple Inc.")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toContain(
      "Takip listesi ve tercihler",
    );
    expect(
      (screen.getByLabelText("Yanıt dili") as HTMLSelectElement).value,
    ).toBe("en");
    expect(
      (screen.getByLabelText("Varsayılan dönem") as HTMLSelectElement).value,
    ).toBe("last_8q");
  });

  it("saves preferences", async () => {
    render(<SettingsPage />);
    const range = (await screen.findByLabelText(
      "Varsayılan dönem",
    )) as HTMLSelectElement;
    fireEvent.change(range, { target: { value: "last_3fy" } });
    fireEvent.change(screen.getByLabelText("Yanıt dili"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Tercihleri kaydet" }));
    expect(await screen.findByText("Tercihler kaydedildi.")).toBeTruthy();
    const put = fetchMock.mock.calls.find(
      ([, init]) => init?.method === "PUT",
    )!;
    expect(String(put[0])).toContain("/v1/me/memory/preferences");
    expect(JSON.parse(String(put[1].body))).toEqual({
      answer_language: null,
      default_period_range: "last_3fy",
    });
  });

  it("deletes all memory only after confirmation", async () => {
    render(<SettingsPage />);
    await screen.findByText("Apple Inc.");
    fireEvent.click(screen.getByRole("button", { name: "Tüm hafızayı sil" }));
    expect(
      screen.getByText(/Takip listendeki 1 şirket ve tercihlerin silinsin mi/),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Vazgeç" }));
    expect(fetchMock.mock.calls.some(([, i]) => i?.method === "DELETE")).toBe(
      false,
    );

    fireEvent.click(screen.getByRole("button", { name: "Tüm hafızayı sil" }));
    fireEvent.click(screen.getByRole("button", { name: "Evet, hepsini sil" }));
    expect(await screen.findByText("Hafızan silindi.")).toBeTruthy();
    expect(screen.getByText(/Takip listen boş/)).toBeTruthy();
    await waitFor(() =>
      expect(
        (screen.getByLabelText("Varsayılan dönem") as HTMLSelectElement).value,
      ).toBe("last_4q"),
    );
  });

  it("shows a load error instead of the form", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "rate_limited" } }), {
        status: 429,
      }),
    );
    render(<SettingsPage />);
    expect(await screen.findByRole("status")).toBeTruthy();
    await waitFor(() =>
      expect(screen.queryByLabelText("Şirket ekle")).toBeNull(),
    );
  });
});
