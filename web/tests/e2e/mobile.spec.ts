import { expect, test, type Page } from "@playwright/test";

import { DEEP_RUN_ID, mockDeepRun } from "./fixtures/deep";
import { mockMemory } from "./fixtures/memory";
import { RUN, RUN_ID, sseBody } from "./fixtures/run";

test.use({ viewport: { width: 375, height: 812 } });

async function noHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(
    () =>
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
  );
  expect(overflow).toBe(0);
}

test.beforeEach(async ({ page }) => {
  await page.route("**/healthz", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        version: "abc1234",
        service: "disclosly-api",
      }),
    }),
  );
  await page.route("**/v1/me", (route) =>
    route.fulfill({ status: 401, body: "{}" }),
  );
  await page.route("**/v1/auth/refresh", (route) =>
    route.fulfill({ status: 401, body: "{}" }),
  );
  await page.route(`**/v1/research/${RUN_ID}/stream`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: sseBody(),
    }),
  );
  await page.route(`**/v1/research/${RUN_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(RUN),
    }),
  );
});

test("ask page fits 375 px", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Sor" })).toBeVisible();
  await noHorizontalScroll(page);
});

test("answer page fits 375 px", async ({ page }) => {
  await page.goto(`/runs/${RUN_ID}`);
  await expect(page.getByTestId("disclaimer")).toBeVisible();
  await expect(page.locator(".answer").first()).toBeVisible();
  await noHorizontalScroll(page);
});

test("sign-in page fits 375 px", async ({ page }) => {
  await page.goto("/signin");
  await expect(
    page.getByRole("button", { name: "Google ile gir" }),
  ).toBeVisible();
  await noHorizontalScroll(page);
});

test("plan dialog and report fit 375 px", async ({ page }) => {
  await mockDeepRun(page);
  await page.goto(`/runs/${DEEP_RUN_ID}`);
  const dialog = page.getByTestId("plan-dialog");
  await expect(dialog).toBeVisible();
  await noHorizontalScroll(page);

  await dialog.getByRole("button", { name: "Planı düzenle" }).click();
  await expect(dialog.getByTestId("plan-step")).toHaveCount(2);
  await noHorizontalScroll(page);

  await dialog.getByRole("button", { name: "Düzenlemeyi bırak" }).click();
  await dialog.getByRole("button", { name: "Onayla ve başlat" }).click();
  await expect(page.getByTestId("report-chart")).toBeVisible();
  await expect(page.getByTestId("report-table")).toBeVisible();
  await noHorizontalScroll(page);
});

test("settings page fits 375 px", async ({ page }) => {
  await mockMemory(page);
  await page.goto("/settings");
  await expect(page.getByTestId("watchlist")).toBeVisible();
  await noHorizontalScroll(page);
  await page.getByLabel("Şirket ekle").fill("a");
  await expect(
    page.getByRole("list", { name: "Arama sonuçları" }),
  ).toBeVisible();
  await noHorizontalScroll(page);
  await page.getByRole("button", { name: "Tüm hafızayı sil" }).click();
  await expect(
    page.getByRole("button", { name: "Evet, hepsini sil" }),
  ).toBeVisible();
  await noHorizontalScroll(page);
});
