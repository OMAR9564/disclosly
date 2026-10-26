import type { Page } from "@playwright/test";

/** A mocked memory API that keeps state across reloads. */

const COMPANIES = [
  { ticker: "ASELS", name: "Aselsan Elektronik", market: "bist" },
  { ticker: "AAPL", name: "Apple Inc.", market: "us" },
  { ticker: "THYAO", name: "Türk Hava Yolları", market: "bist" },
] as const;

export interface Store {
  watchlist: {
    ticker: string;
    name: string;
    market: string;
    added_at: string;
  }[];
  preferences: {
    answer_language: string | null;
    default_period_range: string | null;
  };
}

export async function mockMemory(page: Page): Promise<Store> {
  const store: Store = {
    watchlist: [
      {
        ticker: "THYAO",
        name: "Türk Hava Yolları",
        market: "bist",
        added_at: "2026-10-20T09:00:00Z",
      },
    ],
    preferences: { answer_language: null, default_period_range: null },
  };
  const memory = () => ({ ...store, limits: { watchlist_max: 50 } });
  const json = (body: unknown) => ({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  await page.route("**/v1/auth/refresh", (route) =>
    route.fulfill({ status: 401, body: "{}" }),
  );
  await page.route("**/v1/companies?*", (route) => {
    const q = new URL(route.request().url()).searchParams
      .get("q")
      ?.toLocaleLowerCase("tr");
    const items = COMPANIES.filter(
      (c) =>
        !q ||
        c.ticker.toLowerCase().startsWith(q) ||
        c.name.toLocaleLowerCase("tr").includes(q),
    );
    return route.fulfill(json({ items }));
  });
  await page.route("**/v1/me/memory/watchlist/*", (route) => {
    const ticker = decodeURIComponent(route.request().url().split("/").pop()!);
    store.watchlist = store.watchlist.filter((i) => i.ticker !== ticker);
    return route.fulfill(json(memory()));
  });
  await page.route("**/v1/me/memory/watchlist", (route) => {
    const { ticker } = route.request().postDataJSON() as { ticker: string };
    const company = COMPANIES.find((c) => c.ticker === ticker)!;
    if (!store.watchlist.some((i) => i.ticker === ticker))
      store.watchlist.push({ ...company, added_at: "2026-10-25T09:00:00Z" });
    return route.fulfill(json(memory()));
  });
  await page.route("**/v1/me/memory/preferences", (route) => {
    store.preferences = route.request().postDataJSON() as Store["preferences"];
    return route.fulfill(json(memory()));
  });
  await page.route("**/v1/me/memory", (route) => {
    if (route.request().method() === "DELETE") {
      store.watchlist = [];
      store.preferences = { answer_language: null, default_period_range: null };
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fulfill(json(memory()));
  });
  return store;
}
