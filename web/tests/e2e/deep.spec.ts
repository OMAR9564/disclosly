import { expect, test } from "@playwright/test";

import { DEEP_RUN_ID, mockDeepRun } from "./fixtures/deep";

/** Deep question with a mocked API: plan → edit → approve → timeline → report (T029). */

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
