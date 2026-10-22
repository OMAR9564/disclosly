import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PLAN_MAX_STEPS, PlanDialog } from "@/components/PlanDialog";
import type { Dictionary } from "@/i18n";
import { en } from "@/i18n/en";
import { tr } from "@/i18n/tr";
import {
  ApiError,
  type Decision,
  type DecisionAccepted,
  type Plan,
} from "@/lib/api";
import type { PendingDecision } from "@/lib/runState";

import { PLAN, QUOTAS } from "../e2e/fixtures/deep";

const plan = PLAN as Plan;
const PENDING: PendingDecision = {
  kind: "plan",
  plan,
  estimate: { typical: 6, max: 15 },
  deepRemaining: 4,
  expiresAt: "2026-10-23T08:00:00Z",
};
const ACCEPTED = { run_id: "r1", status: "running" as const, quota: QUOTAS };

function setup(
  pending: PendingDecision = PENDING,
  decide = vi.fn<(decision: Decision) => Promise<DecisionAccepted>>(
    async () => ACCEPTED,
  ),
  t: Dictionary = tr,
) {
  const onDecided = vi.fn();
  render(
    <PlanDialog
      pending={pending}
      plan={pending.plan}
      decide={decide}
      onDecided={onDecided}
      t={t}
    />,
  );
  return { decide, onDecided };
}

const key = (name: string) => screen.getByRole("button", { name });

describe("PlanDialog", () => {
  it("shows the steps, the estimate, the deep runs left and the expiry", () => {
    setup();
    const dialog = screen.getByTestId("plan-dialog");
    expect(dialog.textContent).toContain(
      "THYAO toplam borcu, 2025'in dört çeyreği",
    );
    expect(dialog.textContent).toContain("genelde 6, en çok 15");
    expect(screen.getByTestId("plan-deep-left").textContent).toBe("4");
    expect(dialog.textContent).toContain("23 Ekim 11:00");
    expect(screen.getAllByText("THYAO")).toHaveLength(2);
  });

  it("approves and reports the accepted decision", async () => {
    const { decide, onDecided } = setup();
    fireEvent.click(key("Onayla ve başlat"));
    await waitFor(() => expect(onDecided).toHaveBeenCalledWith(ACCEPTED));
    expect(decide).toHaveBeenCalledWith({ action: "approve" });
  });

  it("cancels", async () => {
    const { decide } = setup();
    fireEvent.click(key("İptal et"));
    await waitFor(() =>
      expect(decide).toHaveBeenCalledWith({ action: "cancel" }),
    );
  });

  it("edits: changes, reorders, removes and adds steps, then sends the plan", async () => {
    const { decide } = setup();
    fireEvent.click(key("Planı düzenle"));

    const goals = screen.getAllByRole("textbox", { name: "Amaç" });
    fireEvent.change(goals[1]!, { target: { value: "  THYAO nakit, 2025  " } });
    fireEvent.change(
      screen.getAllByRole("textbox", { name: "Şirketler" })[1]!,
      {
        target: { value: "thyao, pgsus thyao" },
      },
    );
    fireEvent.change(screen.getAllByRole("combobox", { name: "Tür" })[1]!, {
      target: { value: "search" },
    });
    fireEvent.click(key("Yukarı taşı: Adım 2"));
    fireEvent.click(key("Adım ekle"));
    fireEvent.change(screen.getAllByRole("textbox", { name: "Amaç" })[2]!, {
      target: { value: "PGSUS toplam borç" },
    });
    fireEvent.change(screen.getAllByRole("textbox", { name: "Dönemler" })[2]!, {
      target: { value: "2025-q4" },
    });
    fireEvent.click(key("Adımı kaldır: Adım 2"));

    fireEvent.click(key("Düzenlenmiş planı başlat"));
    await waitFor(() => expect(decide).toHaveBeenCalledTimes(1));
    const sent = decide.mock.calls[0]![0];
    expect(sent.action).toBe("edit");
    expect(sent.plan).toEqual({
      steps: [
        {
          id: "s1",
          kind: "search",
          goal: "THYAO nakit, 2025",
          tickers: ["THYAO", "PGSUS"],
          periods: PLAN.steps[1]!.periods,
          metrics: ["cash_and_equivalents"],
        },
        {
          id: "s2",
          kind: "facts",
          goal: "PGSUS toplam borç",
          tickers: [],
          periods: ["2025-Q4"],
          metrics: [],
        },
      ],
      edited: true,
      revision: 0,
    });
  });

  it("caps the plan at the step limit and keeps the last step", () => {
    setup();
    fireEvent.click(key("Planı düzenle"));
    for (let i = 2; i < PLAN_MAX_STEPS; i++) fireEvent.click(key("Adım ekle"));
    expect(screen.getAllByTestId("plan-step")).toHaveLength(PLAN_MAX_STEPS);
    expect(key("Adım ekle")).toHaveProperty("disabled", true);
    expect(screen.getByText("Plan en çok 8 adım olabilir.")).toBeTruthy();
  });

  it("asks for a goal before sending", () => {
    const { decide } = setup();
    fireEvent.click(key("Planı düzenle"));
    fireEvent.change(screen.getAllByRole("textbox", { name: "Amaç" })[0]!, {
      target: { value: "   " },
    });
    fireEvent.click(key("Düzenlenmiş planı başlat"));
    expect(decide).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toBe(tr.planGoalRequired);
  });

  it("shows plan_invalid reasons on their steps and fields", async () => {
    const decide = vi.fn(async () => {
      throw new ApiError("plan_invalid", "x", 422, null, {
        reasons: [
          { step: "s2", field: "goal", reason: "asks for investment advice" },
          { step: "s1", field: "periods", reason: "bad" },
          { step: null, field: "estimate", reason: "too many calls" },
        ],
      });
    });
    setup(PENDING, decide);
    fireEvent.click(key("Planı düzenle"));
    fireEvent.click(key("Düzenlenmiş planı başlat"));

    await waitFor(() =>
      expect(screen.getAllByTestId("plan-reason")).toHaveLength(2),
    );
    const [goal2] = screen.getAllByRole("textbox", { name: "Amaç" }).slice(1);
    expect(goal2!.getAttribute("aria-invalid")).toBe("true");
    expect(
      document.getElementById(goal2!.getAttribute("aria-describedby")!)
        ?.textContent,
    ).toBe(tr.planReasonAdvice);
    const periods1 = screen.getAllByRole("textbox", { name: "Dönemler" })[0]!;
    expect(periods1.getAttribute("aria-invalid")).toBe("true");
    const alert = screen.getByRole("alert").textContent;
    expect(alert).toContain(tr.planInvalid);
    expect(alert).toContain(tr.planReasonEstimate);
    expect(key("Düzenlenmiş planı başlat")).toHaveProperty("disabled", false);
  });

  it("explains a deep quota refusal at approval", async () => {
    const decide = vi.fn(async () => {
      throw new ApiError("quota_exhausted", "x", 429);
    });
    setup(PENDING, decide, en);
    fireEvent.click(key("Approve and start"));
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toBe(en.errorDeepQuota),
    );
  });

  it("offers simple mode when no deep run is left", async () => {
    const { decide } = setup({
      kind: "deep_quota_offer",
      plan: null,
      estimate: null,
      deepRemaining: 0,
      expiresAt: "2026-10-23T08:00:00Z",
    });
    expect(screen.getByTestId("plan-dialog").dataset.kind).toBe(
      "deep_quota_offer",
    );
    expect(screen.queryByRole("button", { name: "Planı düzenle" })).toBeNull();
    fireEvent.click(key("Basit modda yanıtla"));
    await waitFor(() =>
      expect(decide).toHaveBeenCalledWith({ action: "simple" }),
    );
  });
});
