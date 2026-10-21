import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AnswerView } from "@/components/AnswerView";
import type { Dictionary } from "@/i18n";
import { en } from "@/i18n/en";
import { tr } from "@/i18n/tr";
import type { Answer } from "@/lib/api";

import { ANSWER, CITATIONS } from "./fixtures";

function show(answer: Answer = ANSWER, t: Dictionary = tr) {
  return render(<AnswerView answer={answer} citations={CITATIONS} t={t} />);
}

describe("AnswerView", () => {
  it("renders the summary with marker keys and keeps the text", () => {
    show();
    const summary = screen.getByTestId("answer-summary");
    expect(summary.textContent).toBe(
      "Net kâr 1.234,5 milyon TL oldu 1; artış %23,5 2.",
    );
    expect(
      screen.getAllByRole("button", { name: new RegExp(tr.sourceOpen) }),
    ).toHaveLength(2);
  });

  it("opens and closes a source plate from a summary marker", () => {
    show();
    const key = screen.getByRole("button", { name: `${tr.sourceOpen} 2` });
    fireEvent.click(key);
    const plate = screen.getByTestId("citation");
    expect(plate.textContent).toContain("THYAO 2025-Q3 S.3");
    expect(plate.textContent).toContain("Net dönem kârı %23,5");
    expect(key.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(key);
    expect(screen.queryByTestId("citation")).toBeNull();
  });

  it("renders one arrival row per point with figure and source code", () => {
    show();
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(rows[0]!.textContent).toContain("THYAO 2025-Q3");
    expect(rows[0]!.querySelector(".flaps")!.textContent?.trim()).toBe(
      "1.234,5",
    );
    expect(rows[1]!.querySelector(".flaps")!.textContent?.trim()).toBe("%23,5");
    expect(
      (rows[2]!.querySelector("button") as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("opens a row's plate in place and closes it with Escape", () => {
    show();
    fireEvent.click(screen.getByText("Net dönem kârı 1.234,5 milyon TL"));
    const plate = screen.getByTestId("citation");
    expect(plate.textContent).toContain("[1]");
    expect(plate.textContent).toContain(tr.sourceMetric);
    expect(document.activeElement).toBe(plate);
    const link = screen.getByRole("link", { name: tr.sourceLink });
    expect(link.getAttribute("href")).toBe(
      "https://www.kap.org.tr/tr/Bildirim/1",
    );
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    fireEvent.keyDown(plate, { key: "Escape" });
    expect(screen.queryByTestId("citation")).toBeNull();
  });

  it("closes with the plate key", () => {
    show();
    fireEvent.click(screen.getByText("Yıllık artış %23,5"));
    fireEvent.click(screen.getByRole("button", { name: tr.sourceClose }));
    expect(screen.queryByTestId("citation")).toBeNull();
  });

  it.each([
    ["partial", en.bannerPartial],
    ["refused", en.bannerRefused],
    ["no_evidence", en.bannerNoEvidence],
  ] as const)("shows the %s banner", (status, text) => {
    show({ ...ANSWER, status, points: [], summary: "" }, en);
    expect(screen.getByRole("note").textContent).toBe(text);
    expect(screen.queryByTestId("answer-summary")).toBeNull();
  });

  it("renders the answer's own disclaimer", () => {
    show();
    expect(screen.getByTestId("answer-disclaimer").textContent).toBe(
      "Yatırım tavsiyesi değildir",
    );
  });

  it("shows readable metric and document names on the plate", () => {
    show(ANSWER, en);
    fireEvent.click(screen.getByText("Net dönem kârı 1.234,5 milyon TL"));
    const plate = screen.getByTestId("citation");
    expect(plate.textContent).toContain("Net income");
    expect(plate.textContent).toContain("KAP financial report");
    expect(screen.getByTestId("answer-disclaimer").textContent).toBe(
      "Not investment advice",
    );
  });

  it("leaves unknown markers as text", () => {
    show({ ...ANSWER, summary: "Bilinmeyen [7] işaret.", points: [] });
    expect(screen.getByTestId("answer-summary").textContent).toBe(
      "Bilinmeyen [7] işaret.",
    );
  });
});
