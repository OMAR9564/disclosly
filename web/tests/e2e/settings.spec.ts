import { expect, test, type Page } from "@playwright/test";

/** Watchlist and settings with a mocked API that keeps state across reloads (T049). */

const COMPANIES = [
  { ticker: "ASELS", name: "Aselsan Elektronik", market: "bist" },
  { ticker: "AAPL", name: "Apple Inc.", market: "us" },
  { ticker: "THYAO", name: "Türk Hava Yolları", market: "bist" },
] as const;

interface Store {
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

async function mockMemory(page: Page): Promise<Store> {
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

test("add, reload, remove, change preferences and delete all", async ({
  page,
}) => {
  const store = await mockMemory(page);
  await page.goto("/settings");

  const list = page.getByTestId("watchlist");
  await expect(list.getByText("Türk Hava Yolları")).toBeVisible();

  await page.getByLabel("Şirket ekle").fill("asel");
  await page
    .getByRole("button", { name: "ASELS şirketini takip listesine ekle" })
    .click();
  await expect(list.getByText("Aselsan Elektronik")).toBeVisible();

  await page.reload();
  await expect(
    page.getByTestId("watchlist").getByText("ASELS", { exact: true }),
  ).toBeVisible();

  await page
    .getByRole("button", { name: "THYAO şirketini listeden çıkar" })
    .click();
  await expect(
    page.getByTestId("watchlist").getByText("THYAO", { exact: true }),
  ).toHaveCount(0);

  await page.getByLabel("Varsayılan dönem").selectOption("last_2fy");
  await page.getByRole("button", { name: "Tercihleri kaydet" }).click();
  await expect(page.getByText("Tercihler kaydedildi.")).toBeVisible();
  expect(store.preferences.default_period_range).toBe("last_2fy");

  await page.reload();
  await expect(page.getByLabel("Varsayılan dönem")).toHaveValue("last_2fy");

  await page.getByRole("button", { name: "Tüm hafızayı sil" }).click();
  await page.getByRole("button", { name: "Evet, hepsini sil" }).click();
  await expect(page.getByText("Hafızan silindi.")).toBeVisible();
  await expect(page.getByText(/Takip listen boş/)).toBeVisible();
  await page.reload();
  await expect(page.getByText(/Takip listen boş/)).toBeVisible();
  expect(store.watchlist).toEqual([]);
});

test("phone width: no sideways scroll", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockMemory(page);
  await page.goto("/settings");
  await page.getByLabel("Şirket ekle").fill("a");
  await expect(
    page.getByRole("list", { name: "Arama sonuçları" }),
  ).toBeVisible();
  const overflow = await page.evaluate(
    () =>
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
  );
  expect(overflow).toBe(0);
});
