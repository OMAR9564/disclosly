import { expect, test, type Page } from "@playwright/test";

import { QUESTION, RUN, RUN_ID, sseBody } from "./fixtures/run";

/** Mocked API (route interception): ask, timeline, answer, citation, disclaimer. */
async function mockApi(page: Page) {
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
  await page.route("**/v1/research", (route) =>
    route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ run_id: RUN_ID }),
    }),
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
}

test("ask → timeline → answer → citation → disclaimer", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");

  await page.getByLabel("Soru").fill(QUESTION);
  await page.getByRole("button", { name: "Sor" }).click();

  await expect(page).toHaveURL(new RegExp(`/runs/${RUN_ID}$`));
  await expect(page.getByTestId("question")).toHaveText(QUESTION);

  const timeline = page.getByTestId("timeline");
  await expect(timeline).toContainText("Kontrol: Tamam");

  const answer = page.getByTestId("answer");
  await expect(answer).toContainText("1.234,5 milyon TL");

  await page.getByRole("button", { name: "Kaynağı göster 2" }).click();
  const citation = page.getByTestId("citation");
  await expect(citation).toBeVisible();
  await expect(citation).toContainText("THYAO 2025-Q3 S.3");
  await expect(citation).toContainText("%23,5 artışla");
  await expect(citation).not.toContainText("_");
  await expect(
    citation.getByRole("link", { name: "Belgeyi aç" }),
  ).toHaveAttribute("href", "https://www.kap.org.tr/tr/Bildirim/1");

  // The router keeps the previous page mounted but hidden; match the visible plate.
  const disclaimer = page.getByTestId("disclaimer").filter({ visible: true });
  await expect(disclaimer).toBeVisible();
  await expect(disclaimer).toContainText("Yatırım tavsiyesi değildir");
});

test("reloading a run replays it", async ({ page }) => {
  await mockApi(page);
  await page.goto(`/runs/${RUN_ID}`);
  await expect(page.getByTestId("answer")).toContainText("Yıllık artış %23,5");
  await page.reload();
  await expect(page.getByTestId("answer")).toContainText("Yıllık artış %23,5");
  await expect(page.getByTestId("disclaimer")).toBeVisible();
});
