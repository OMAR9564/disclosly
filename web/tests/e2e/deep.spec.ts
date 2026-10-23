import { expect, test, type Page } from "@playwright/test";

import {
  AFTER_DECISION,
  BEFORE_DECISION,
  DEEP_RUN,
  DEEP_RUN_ID,
  QUOTAS,
} from "./fixtures/deep";
import { sseBody } from "./fixtures/run";

/** Deep question with a mocked API: plan → edit → approve → timeline → report (T029). */

async function mockDeepRun(page: Page): Promise<{ decisions: unknown[] }> {
  const decisions: unknown[] = [];
  let streams = 0;
  await page.route("**/healthz", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok", version: "abc1234" }),
    }),
  );
  await page.route("**/v1/auth/refresh", (route) =>
    route.fulfill({ status: 401, body: "{}" }),
  );
  await page.route(`**/v1/research/${DEEP_RUN_ID}/stream`, (route) => {
    streams += 1;
    // The first stream ends at the pause; after the decision a new EventSource cannot send
    // Last-Event-ID, so the server replays from the start and the client skips what it saw.
    const events =
      streams === 1 ? BEFORE_DECISION : [...BEFORE_DECISION, ...AFTER_DECISION];
    return route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: sseBody(events),
    });
  });
  await page.route(`**/v1/research/${DEEP_RUN_ID}/approve`, async (route) => {
    decisions.push(route.request().postDataJSON());
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({
        run_id: DEEP_RUN_ID,
        status: "running",
        quota: QUOTAS,
      }),
    });
  });
  await page.route(`**/v1/research/${DEEP_RUN_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(DEEP_RUN),
    }),
  );
  return { decisions };
}

async function noHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(
    () =>
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
  );
  expect(overflow).toBe(0);
}

test("deep question: edit the plan, approve, read the report", async ({
  page,
}) => {
  const { decisions } = await mockDeepRun(page);
  await page.goto(`/runs/${DEEP_RUN_ID}`);

  const dialog = page.getByTestId("plan-dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("genelde 6, en çok 15")).toBeVisible();
  await expect(dialog.getByTestId("plan-deep-left")).toHaveText("4");
  await expect(page.getByText("Plan kararını bekliyor.")).toBeVisible();

  await dialog.getByRole("button", { name: "Planı düzenle" }).click();
  const goal = dialog.getByRole("textbox", { name: "Amaç" }).nth(1);
  await goal.fill("THYAO nakit ve nakit benzerleri, 2025 çeyrekleri");
  await dialog
    .getByRole("button", { name: "Düzenlenmiş planı başlat" })
    .click();

  await expect(dialog).toBeHidden();
  expect(decisions).toHaveLength(1);
  expect(decisions[0]).toMatchObject({
    action: "edit",
    plan: {
      steps: [
        { id: "s1", kind: "facts" },
        {
          id: "s2",
          goal: "THYAO nakit ve nakit benzerleri, 2025 çeyrekleri",
          tickers: ["THYAO"],
        },
      ],
      edited: true,
    },
  });

  const timeline = page.getByTestId("timeline");
  await expect(timeline.getByText("Rakam: 8")).toBeAttached();
  await expect(timeline.getByText("Model: 3")).toBeAttached();

  const report = page.getByTestId("answer");
  await expect(report.getByTestId("report-table")).toBeVisible();
  await expect(report.getByTestId("report-chart")).toBeVisible();
  await expect(report.getByTestId("report-confidence")).toContainText(
    "Dört çeyreğin tamamı",
  );

  await report
    .getByRole("button", { name: "Kaynağı göster 2: 15,6 milyar TL" })
    .click();
  await expect(page.getByTestId("citation")).toContainText(
    "Nakit ve nakit benzerleri (2025-Q4): 15,6 milyar TRY",
  );
  await expect(page.getByTestId("answer-disclaimer")).toHaveText(
    "Yatırım tavsiyesi değildir",
  );
});

test.describe("phone", () => {
  test.use({ viewport: { width: 375, height: 812 } });

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
    await noHorizontalScroll(page);
  });
});
