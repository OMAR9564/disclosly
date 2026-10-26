import { expect, test } from "@playwright/test";

import { mockMemory } from "./fixtures/memory";

/** Watchlist and settings with a mocked API that keeps state across reloads (T049). */

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
