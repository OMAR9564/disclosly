import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  QuotaBadge,
  quotaExhaustedMessage,
  resetLabel,
} from "@/components/QuotaBadge";
import { en } from "@/i18n/en";
import { tr } from "@/i18n/tr";

const RESETS = "2026-10-21T21:00:00Z"; // 00:00 in Istanbul
const DEEP = { limit: 5, used: 1, remaining: 4, resets_at: RESETS };

describe("QuotaBadge", () => {
  it("shows the remaining count as text for assistive technology", () => {
    render(
      <QuotaBadge
        quota={{
          simple: { limit: 20, used: 3, remaining: 17, resets_at: RESETS },
          deep: DEEP,
        }}
        t={tr}
      />,
    );
    expect(screen.getByTestId("quota-badge").textContent).toContain(
      "17 / 20 soru kaldı",
    );
    expect(screen.getByTestId("quota-deep").textContent).toContain(
      "4 / 5 derin araştırma kaldı",
    );
  });

  it("shows zero when exhausted", () => {
    render(
      <QuotaBadge
        quota={{
          simple: { limit: 20, used: 20, remaining: 0, resets_at: RESETS },
          deep: { ...DEEP, used: 5, remaining: 0 },
        }}
        t={en}
      />,
    );
    expect(screen.getByTestId("quota-badge").textContent).toContain(
      "0 / 20 questions left",
    );
    expect(screen.getByTestId("quota-deep").textContent).toContain(
      "0 / 5 deep research runs left",
    );
  });

  it("reset time is Istanbul midnight in both languages", () => {
    expect(resetLabel(RESETS, tr)).toBe("00:00 (TSİ)");
    expect(resetLabel(RESETS, en)).toBe("00:00 (TRT)");
    const quota = { limit: 2, used: 2, remaining: 0, resets_at: RESETS };
    expect(quotaExhaustedMessage(quota, en)).toBe(
      "You have used today's questions. The quota resets at 00:00 (TRT).",
    );
  });
});
