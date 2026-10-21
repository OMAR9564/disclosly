import { expect, test, type Page } from "@playwright/test";

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
