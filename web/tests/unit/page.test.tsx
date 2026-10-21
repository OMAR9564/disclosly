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

describe("Home ask", () => {
  it("starts a run and goes to its page", async () => {
    const { routerPush } = await import("./setup");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        String(url).endsWith("/v1/research")
          ? new Response(JSON.stringify({ run_id: "run-9" }), { status: 202 })
          : new Response(
              JSON.stringify({
                status: "ok",
                version: "abc1234",
                service: "disclosly-api",
              }),
              { status: 200 },
            ),
      ),
    );
    render(<Home />);
    fireEvent.change(screen.getByLabelText(tr.askLabel), {
      target: { value: "THYAO net kâr?" },
    });
    fireEvent.click(screen.getByRole("button", { name: tr.askSubmit }));
    await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/runs/run-9"));
  });

  it("shows a localized error when asking fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        String(url).endsWith("/v1/research")
          ? new Response(
              JSON.stringify({ error: { code: "unauthorized", message: "x" } }),
              { status: 401 },
            )
          : new Response("{}", { status: 503 }),
      ),
    );
    render(<Home />);
    fireEvent.change(screen.getByLabelText(tr.askLabel), {
      target: { value: "THYAO net kâr?" },
    });
    fireEvent.click(screen.getByRole("button", { name: tr.askSubmit }));
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toBe(tr.errorUnauthorized),
    );
  });
});
