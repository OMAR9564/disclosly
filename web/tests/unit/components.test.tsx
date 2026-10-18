import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BackendStatus } from "@/components/BackendStatus";
import { Disclaimer } from "@/components/Disclaimer";
import { FlapRow } from "@/components/FlapRow";
import { LanguageToggle } from "@/components/LanguageToggle";
import { en } from "@/i18n/en";
import { tr } from "@/i18n/tr";

describe("BackendStatus", () => {
  it("shows the loading state", () => {
    render(<BackendStatus health={{ state: "loading" }} t={tr} />);
    const el = screen.getByTestId("backend-status");
    expect(el.dataset.state).toBe("loading");
    expect(el.textContent).toContain(tr.statusLoading);
  });

  it("shows the version when available", () => {
    render(
      <BackendStatus
        health={{ state: "available", version: "abc1234def" }}
        t={en}
      />,
    );
    const el = screen.getByTestId("backend-status");
    expect(el.dataset.state).toBe("available");
    expect(el.textContent).toContain("abc1234");
    expect(el.textContent).toContain(en.statusAvailable);
  });

  it("shows a friendly message when unavailable", () => {
    render(<BackendStatus health={{ state: "unavailable" }} t={tr} />);
    const el = screen.getByTestId("backend-status");
    expect(el.dataset.state).toBe("unavailable");
    expect(el.textContent).toContain(tr.statusUnavailable);
  });
});

describe("LanguageToggle", () => {
  it("is a native button that calls onToggle", () => {
    const onToggle = vi.fn();
    render(<LanguageToggle t={tr} onToggle={onToggle} />);
    const button = screen.getByTestId("language-toggle");
    expect(button.tagName).toBe("BUTTON");
    expect(button.textContent).toBe("English");
    fireEvent.click(button);
    expect(onToggle).toHaveBeenCalledOnce();
  });
});

describe("Disclaimer", () => {
  it("always renders the disclaimer text", () => {
    render(<Disclaimer t={en} />);
    expect(screen.getByTestId("disclaimer").textContent).toContain(
      "Not investment advice",
    );
  });
});

describe("FlapRow", () => {
  it("settles on the padded, upper-cased text after spinning", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    const { container, rerender } = render(
      <FlapRow text="ok" length={4} locale="tr" />,
    );
    rerender(<FlapRow text="çalış" length={4} locale="tr" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    const cells = Array.from(
      container.querySelectorAll(".flap"),
      (cell) => cell.textContent,
    );
    expect(cells).toEqual(["Ç", "A", "L", "I"]);
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("keeps a dotless I for the brand name", () => {
    const { container } = render(<FlapRow text="Disclosly" length={9} />);
    expect(container.textContent).toBe("DISCLOSLY");
  });

  it("marks blank cells and the live tone", () => {
    const { container } = render(<FlapRow text="a" length={2} tone="live" />);
    expect(container.querySelector(".flaps--live")).not.toBeNull();
    expect(container.querySelectorAll(".flap--blank")).toHaveLength(1);
  });
});
