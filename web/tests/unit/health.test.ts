import { afterEach, describe, expect, it, vi } from "vitest";

import { HEALTH_TIMEOUT_MS, fetchHealth } from "@/lib/health";

const okBody = { status: "ok", version: "abc1234", service: "disclosly-api" };

function mockFetch(impl: typeof fetch) {
  vi.stubGlobal("fetch", vi.fn(impl));
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("fetchHealth", () => {
  it("returns available with the version for a valid 200", async () => {
    mockFetch(
      async () => new Response(JSON.stringify(okBody), { status: 200 }),
    );
    await expect(fetchHealth("http://api.test")).resolves.toEqual({
      state: "available",
      version: "abc1234",
    });
    expect(fetch).toHaveBeenCalledWith(
      "http://api.test/healthz",
      expect.anything(),
    );
  });

  it("returns unavailable for a non-200", async () => {
    mockFetch(
      async () => new Response(JSON.stringify(okBody), { status: 503 }),
    );
    await expect(fetchHealth("http://api.test")).resolves.toEqual({
      state: "unavailable",
    });
  });

  it.each([
    ["wrong service", { ...okBody, service: "other" }],
    ["missing version", { status: "ok", service: "disclosly-api" }],
    ["wrong status", { ...okBody, status: "down" }],
    ["not an object", ["ok"]],
  ])("returns unavailable for an invalid body (%s)", async (_name, body) => {
    mockFetch(async () => new Response(JSON.stringify(body), { status: 200 }));
    await expect(fetchHealth("http://api.test")).resolves.toEqual({
      state: "unavailable",
    });
  });

  it("returns unavailable for a non-JSON body", async () => {
    mockFetch(async () => new Response("<html>", { status: 200 }));
    await expect(fetchHealth("http://api.test")).resolves.toEqual({
      state: "unavailable",
    });
  });

  it("returns unavailable for a network error", async () => {
    mockFetch(async () => {
      throw new TypeError("Failed to fetch");
    });
    await expect(fetchHealth("http://api.test")).resolves.toEqual({
      state: "unavailable",
    });
  });

  it("returns unavailable after the timeout", async () => {
    vi.useFakeTimers();
    mockFetch(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    const result = fetchHealth("http://api.test");
    await vi.advanceTimersByTimeAsync(HEALTH_TIMEOUT_MS);
    await expect(result).resolves.toEqual({ state: "unavailable" });
  });
});
