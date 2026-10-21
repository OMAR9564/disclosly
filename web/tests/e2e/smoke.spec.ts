import { expect, test, type Page } from "@playwright/test";

const validBody = {
  status: "ok",
  version: "abc1234def5678",
  service: "disclosly-api",
};

async function routeHealth(page: Page, status: number, body: unknown) {
  await page.route("**/healthz", (route) =>
    route.fulfill({
      status,
      contentType: "application/json",
      headers: { "access-control-allow-origin": "*" },
      body: JSON.stringify(body),
    }),
  );
}

test("loads in Turkish with the disclaimer visible", async ({ page }) => {
  await routeHealth(page, 200, validBody);
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("lang", "tr");
  await expect(page.getByTestId("tagline")).toHaveText(
    "KAP ve SEC açıklamaları üzerine kaynak gösteren araştırma asistanı",
  );
  await expect(page.getByTestId("disclaimer")).toBeVisible();
  await expect(page.getByTestId("disclaimer")).toContainText(
    "Yatırım tavsiyesi değildir",
  );
});

test("switches to English and keeps the choice after a reload", async ({
  page,
}) => {
  await routeHealth(page, 200, validBody);
  await page.goto("/");
  await page.getByTestId("language-toggle").click();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByTestId("disclaimer")).toContainText(
    "Not investment advice",
  );

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByTestId("language-toggle")).toHaveText("Türkçe");
});

test("shows unavailable when /healthz returns 503", async ({ page }) => {
  await routeHealth(page, 503, { detail: "down" });
  await page.goto("/");
  await expect(page.getByTestId("backend-status")).toHaveAttribute(
    "data-state",
    "unavailable",
  );
});

test("shows available with the version for a valid body", async ({ page }) => {
  await routeHealth(page, 200, validBody);
  await page.goto("/");
  const status = page.getByTestId("backend-status");
  await expect(status).toHaveAttribute("data-state", "available");
  await expect(status).toContainText("abc1234");
});

test("has no horizontal scroll at 360 px", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await routeHealth(page, 200, validBody);
  await page.goto("/");
  await expect(page.getByTestId("backend-status")).toHaveAttribute(
    "data-state",
    "available",
  );
  const overflow = await page.evaluate(
    () =>
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
  );
  expect(overflow).toBe(0);
});

for (const [path, tr, en] of [
  ["/privacy", "Gizlilik politikası", "Privacy policy"],
  ["/terms", "Kullanım koşulları", "Terms of use"],
  ["/disclaimer", "Yasal uyarı", "Disclaimer"],
] as const) {
  test(`opens ${path} signed out in both languages`, async ({ page }) => {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(tr);
    await page.getByTestId("language-toggle").click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(en);
    await expect(page.getByTestId("disclaimer")).toBeVisible();
  });
}
