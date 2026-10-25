import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WatchlistEditor } from "@/components/WatchlistEditor";
import { en } from "@/i18n/en";
import { tr } from "@/i18n/tr";
import type { Memory } from "@/lib/api";

const THYAO = {
  ticker: "THYAO",
  name: "Türk Hava Yolları",
  market: "bist" as const,
  added_at: "2026-10-20T09:00:00Z",
};

function memory(watchlist = [THYAO], max = 50): Memory {
  return {
    watchlist,
    preferences: { answer_language: null, default_period_range: null },
    limits: { watchlist_max: max },
  };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("WatchlistEditor", () => {
  it("lists companies with ticker, name and market and counts them", () => {
    render(
      <WatchlistEditor
        memory={memory()}
        onChange={() => {}}
        language="tr"
        t={tr}
      />,
    );
    const list = screen.getByTestId("watchlist");
    expect(list.textContent).toContain("THYAO");
    expect(list.textContent).toContain("Türk Hava Yolları");
    expect(list.textContent).toContain("Borsa İstanbul");
    expect(screen.getByText("1 / 50 şirket")).toBeTruthy();
  });

  it("says when the watchlist is empty", () => {
    render(
      <WatchlistEditor
        memory={memory([])}
        onChange={() => {}}
        language="en"
        t={en}
      />,
    );
    expect(screen.getByText(/Your watchlist is empty/)).toBeTruthy();
  });

  it("searches after a pause and adds a company", async () => {
    const onChange = vi.fn();
    const asels = { ticker: "ASELS", name: "Aselsan", market: "bist" };
    fetchMock.mockImplementation(async (url: string, init: RequestInit) => {
      if (url.includes("/v1/companies"))
        return json({ items: [asels, { ...THYAO, added_at: undefined }] });
      expect(init.method).toBe("POST");
      expect(JSON.parse(String(init.body))).toEqual({ ticker: "ASELS" });
      return json(memory([THYAO, { ...asels, market: "bist", added_at: "x" }]));
    });
    render(
      <WatchlistEditor
        memory={memory()}
        onChange={onChange}
        language="tr"
        t={tr}
      />,
    );
    fireEvent.change(screen.getByLabelText("Şirket ekle"), {
      target: { value: "asel" },
    });
    expect(fetchMock).not.toHaveBeenCalled();
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    const add = await screen.findByRole("button", {
      name: "ASELS şirketini takip listesine ekle",
    });
    expect(String(fetchMock.mock.calls[0][0])).toContain("q=asel");
    // A company already listed cannot be added twice.
    expect(
      (screen.getByRole("button", { name: "Listede" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    fireEvent.click(add);
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(onChange.mock.calls[0][0].watchlist).toHaveLength(2);
  });

  it("removes a company", async () => {
    const onChange = vi.fn();
    fetchMock.mockResolvedValue(json(memory([])));
    render(
      <WatchlistEditor
        memory={memory()}
        onChange={onChange}
        language="tr"
        t={tr}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "THYAO şirketini listeden çıkar" }),
    );
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/v1/me/memory/watchlist/THYAO");
    expect(init.method).toBe("DELETE");
  });

  it("shows the API error under the field, without alarm colors", async () => {
    fetchMock.mockImplementation(async (url: string) =>
      url.includes("/v1/companies")
        ? json({ items: [{ ticker: "ZZZZ", name: "Z", market: "us" }] })
        : json({ error: { code: "company_not_covered", message: "x" } }, 422),
    );
    render(
      <WatchlistEditor
        memory={memory()}
        onChange={() => {}}
        language="tr"
        t={tr}
      />,
    );
    fireEvent.change(screen.getByLabelText("Şirket ekle"), {
      target: { value: "z" },
    });
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    fireEvent.click(
      await screen.findByRole("button", {
        name: "ZZZZ şirketini takip listesine ekle",
      }),
    );
    expect(
      await screen.findByText("Bu şirket Disclosly kapsamında değil."),
    ).toBeTruthy();
  });

  it("disables adding when the watchlist is full", async () => {
    fetchMock.mockResolvedValue(
      json({ items: [{ ticker: "AAPL", name: "Apple", market: "us" }] }),
    );
    render(
      <WatchlistEditor
        memory={memory([THYAO], 1)}
        onChange={() => {}}
        language="en"
        t={en}
      />,
    );
    expect(
      screen.getByText(/Your watchlist is full \(1 companies\)/),
    ).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Add a company"), {
      target: { value: "a" },
    });
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    const add = await screen.findByRole("button", {
      name: "Add AAPL to the watchlist",
    });
    expect((add as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows no results for an empty query and a note for no match", async () => {
    fetchMock.mockResolvedValue(json({ items: [] }));
    render(
      <WatchlistEditor
        memory={memory()}
        onChange={() => {}}
        language="tr"
        t={tr}
      />,
    );
    const field = screen.getByLabelText("Şirket ekle");
    fireEvent.change(field, { target: { value: "qq" } });
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    expect(await screen.findByText(tr.companySearchNone)).toBeTruthy();
    fireEvent.change(field, { target: { value: " " } });
    expect(screen.queryByText(tr.companySearchNone)).toBeNull();
  });
});
