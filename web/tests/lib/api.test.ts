import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  getMe,
  navigation,
  startResearch,
  startSignIn,
} from "@/lib/api";

const QUOTA = {
  limit: 20,
  used: 1,
  remaining: 19,
  resets_at: "2026-10-21T21:00:00Z",
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const unauthorized = () =>
  json(401, { error: { code: "unauthorized", message: "x", request_id: "r" } });

let assign: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  assign = vi.spyOn(navigation, "assign").mockImplementation(() => undefined);
  window.history.replaceState(null, "", "/runs/abc?x=1");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("silent refresh", () => {
  it("refreshes once and retries the request", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(unauthorized())
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(json(202, { run_id: "r1", quota: QUOTA }));
    vi.stubGlobal("fetch", fetchMock);

    const started = await startResearch("THYAO net kâr?", "tr", "https://api");

    expect(started.run_id).toBe("r1");
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual([
      "https://api/v1/research",
      "https://api/v1/auth/refresh",
      "https://api/v1/research",
    ]);
    expect(fetchMock.mock.calls[1][1]).toMatchObject({
      method: "POST",
      credentials: "include",
    });
    expect(assign).not.toHaveBeenCalled();
  });

  it("goes to sign-in with return_to when the refresh fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(unauthorized())
      .mockResolvedValueOnce(unauthorized());
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      startResearch("THYAO?", "tr", "https://api"),
    ).rejects.toMatchObject({
      code: "unauthorized",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(assign).toHaveBeenCalledWith(
      `/signin?return_to=${encodeURIComponent("/runs/abc?x=1")}`,
    );
  });

  it("retries only once", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(unauthorized())
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(unauthorized());
    vi.stubGlobal("fetch", fetchMock);
    await expect(
      startResearch("THYAO?", "tr", "https://api"),
    ).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("sends non-listed users to the beta page", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        json(403, {
          error: { code: "beta_closed", message: "x", request_id: "r" },
        }),
      ),
    );
    await expect(
      startResearch("THYAO?", "tr", "https://api"),
    ).rejects.toMatchObject({
      code: "beta_closed",
    });
    expect(assign).toHaveBeenCalledWith("/beta-closed");
  });
});

describe("me and sign-in", () => {
  it("getMe never redirects a signed-out visitor", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(unauthorized())
        .mockResolvedValueOnce(unauthorized()),
    );
    await expect(getMe("tr", "https://api")).rejects.toMatchObject({
      code: "unauthorized",
    });
    expect(assign).not.toHaveBeenCalled();
  });

  it("startSignIn posts the token and return_to without a refresh", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        json(200, { authorize_url: "https://accounts.example/auth" }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const result = await startSignIn(
      "github",
      "tok",
      "/ask",
      "en",
      "https://api",
    );
    expect(result.authorize_url).toBe("https://accounts.example/auth");
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api/v1/auth/github/start");
    expect(JSON.parse(init.body)).toEqual({
      turnstile_token: "tok",
      return_to: "/ask",
    });
  });

  it("a rejected sign-in start does not redirect", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        json(403, {
          error: { code: "bot_check_failed", message: "x", request_id: "r" },
        }),
      ),
    );
    await expect(
      startSignIn("google", "t", "/", "tr", "https://api"),
    ).rejects.toMatchObject({
      code: "bot_check_failed",
    });
    expect(assign).not.toHaveBeenCalled();
  });
});
