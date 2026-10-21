import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BetaClosed } from "@/components/BetaClosed";
import { SignIn, safeReturnTo, signInError } from "@/components/SignIn";
import { en } from "@/i18n/en";
import { tr } from "@/i18n/tr";
import { navigation } from "@/lib/api";

import { searchParams } from "../unit/setup";

type Options = {
  callback: (token: string) => void;
  "error-callback": () => void;
};
let widget: Options | null = null;

beforeEach(() => {
  widget = null;
  window.turnstile = {
    render: (_el, options) => {
      widget = options;
      return "w1";
    },
    remove: vi.fn(),
  };
  searchParams.value = new URLSearchParams();
});

afterEach(() => {
  delete window.turnstile;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const googleKey = () => screen.getByRole("button", { name: tr.signinGoogle });

describe("SignIn", () => {
  it("keeps the keys locked until the bot check passes", async () => {
    render(<SignIn siteKey="site" />);
    expect((googleKey() as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(tr.signinBotWaiting)).toBeTruthy();
    await waitFor(() => expect(widget).not.toBeNull());
    act(() => widget!.callback("tok"));
    expect((googleKey() as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByText(tr.signinBotReady)).toBeTruthy();
  });

  it("starts the flow and leaves for the provider", async () => {
    searchParams.value = new URLSearchParams({ return_to: "/runs/r1" });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ authorize_url: "https://accounts.example/a" }),
        {
          status: 200,
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const assign = vi
      .spyOn(navigation, "assign")
      .mockImplementation(() => undefined);
    render(<SignIn siteKey="site" />);
    await waitFor(() => expect(widget).not.toBeNull());
    act(() => widget!.callback("tok"));
    fireEvent.click(googleKey());
    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith("https://accounts.example/a"),
    );
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      turnstile_token: "tok",
      return_to: "/runs/r1",
    });
  });

  it("shows a failed bot check and locks the keys again", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: "bot_check_failed",
              message: "x",
              request_id: "r",
            },
          }),
          { status: 403 },
        ),
      ),
    );
    render(<SignIn siteKey="site" />);
    await waitFor(() => expect(widget).not.toBeNull());
    act(() => widget!.callback("tok"));
    fireEvent.click(googleKey());
    expect((await screen.findByRole("alert")).textContent).toContain(
      tr.signinErrorBot,
    );
    expect((googleKey() as HTMLButtonElement).disabled).toBe(true);
  });

  it("reports a widget that cannot load", async () => {
    render(<SignIn siteKey="site" />);
    await waitFor(() => expect(widget).not.toBeNull());
    act(() => widget!["error-callback"]());
    expect(screen.getByText(tr.signinBotFailed)).toBeTruthy();
  });

  it("shows the callback error from the URL", () => {
    searchParams.value = new URLSearchParams({ error: "email_unverified" });
    render(<SignIn siteKey="site" />);
    expect(screen.getByRole("alert").textContent).toContain(
      tr.signinErrorEmail,
    );
  });

  it("links to privacy and terms", () => {
    render(<SignIn siteKey="site" />);
    expect(
      screen.getByRole("link", { name: tr.privacyLink }).getAttribute("href"),
    ).toBe("/privacy");
    expect(
      screen.getByRole("link", { name: tr.termsLink }).getAttribute("href"),
    ).toBe("/terms");
  });
});

describe("sign-in helpers", () => {
  it("maps callback reasons", () => {
    expect(signInError(null, tr)).toBeNull();
    expect(signInError("invalid_state", en)).toBe(en.signinErrorState);
    expect(signInError("provider_error", en)).toBe(en.signinErrorProvider);
  });

  it.each([
    [null, "/"],
    ["/ask", "/ask"],
    ["//evil.example", "/"],
    ["https://evil.example", "/"],
    ["/\\evil", "/"],
  ])("safeReturnTo(%s) = %s", (value, expected) => {
    expect(safeReturnTo(value)).toBe(expected);
  });
});

describe("BetaClosed", () => {
  it("explains the closed beta and links home", () => {
    render(<BetaClosed />);
    expect(screen.getByText(tr.betaBody)).toBeTruthy();
    expect(
      screen.getByRole("link", { name: tr.betaHome }).getAttribute("href"),
    ).toBe("/");
    expect(screen.getByTestId("disclaimer")).toBeTruthy();
  });
});
