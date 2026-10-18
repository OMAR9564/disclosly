import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import RootLayout from "@/app/layout";
import Home from "@/app/page";
import { en } from "@/i18n/en";
import { tr } from "@/i18n/tr";

vi.mock("next/font/google", () => ({
  Archivo: () => ({ variable: "font-archivo", className: "font-archivo" }),
}));

const TEST_IDS = [
  "product-name",
  "tagline",
  "disclaimer",
  "backend-status",
  "language-toggle",
];

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            status: "ok",
            version: "abc1234",
            service: "disclosly-api",
          }),
          { status: 200 },
        ),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
  document.documentElement.lang = "";
});

describe("Home", () => {
  it("renders every test id in Turkish", async () => {
    render(<Home />);
    for (const id of TEST_IDS) expect(screen.getByTestId(id)).toBeDefined();
    expect(
      screen.getByRole("heading", { level: 1, name: "Disclosly" }),
    ).toBeDefined();
    expect(screen.getByTestId("tagline").textContent).toBe(tr.tagline);
    await waitFor(() =>
      expect(screen.getByTestId("backend-status").dataset.state).toBe(
        "available",
      ),
    );
    expect(document.documentElement.lang).toBe("tr");
  });

  it("switches all visible text to English", async () => {
    render(<Home />);
    await waitFor(() =>
      expect(screen.getByTestId("backend-status").dataset.state).toBe(
        "available",
      ),
    );
    act(() => {
      fireEvent.click(screen.getByTestId("language-toggle"));
    });
    expect(screen.getByTestId("tagline").textContent).toBe(en.tagline);
    expect(screen.getByTestId("disclaimer").textContent).toContain(
      en.disclaimer,
    );
    expect(screen.getByTestId("backend-status").textContent).toContain(
      en.statusAvailable,
    );
    expect(screen.getByTestId("language-toggle").textContent).toBe(
      en.toggleLabel,
    );
    expect(document.documentElement.lang).toBe("en");
    expect(document.body.textContent).not.toContain(tr.tagline);
  });
});

describe("RootLayout", () => {
  it("defaults the document language to Turkish", () => {
    const html = RootLayout({ children: null, params: Promise.resolve({}) });
    expect(html.props.lang).toBe("tr");
  });
});
