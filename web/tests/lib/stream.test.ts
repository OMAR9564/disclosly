import { describe, expect, it, vi } from "vitest";

import { ApiError, getRun, startResearch, streamUrl } from "@/lib/api";
import { MAX_RECONNECTS, type RunEvent, subscribeRun } from "@/lib/stream";

class MockEventSource {
  static last: MockEventSource | null = null;
  readonly listeners = new Map<string, ((event: MessageEvent) => void)[]>();
  readyState = 1;
  closed = false;
  onerror: (() => void) | null = null;

  constructor(
    readonly url: string,
    readonly init: EventSourceInit,
  ) {
    MockEventSource.last = this;
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close() {
    this.closed = true;
    this.readyState = 2;
  }

  emit(type: string, data: unknown, id = "1-0") {
    const event = new MessageEvent(type, {
      data: typeof data === "string" ? data : JSON.stringify(data),
      lastEventId: id,
    });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

function open(onDisconnect = vi.fn()) {
  const events: RunEvent[] = [];
  const close = subscribeRun(
    "http://api.test/v1/research/r1/stream",
    { onEvent: (e) => events.push(e), onDisconnect },
    (url, init) => new MockEventSource(url, init) as unknown as EventSource,
  );
  return { events, close, source: MockEventSource.last!, onDisconnect };
}

describe("subscribeRun", () => {
  it("opens with credentials and emits typed events in order", () => {
    const { events, source } = open();
    expect(source.init).toEqual({ withCredentials: true });
    source.emit("run_started", { run_id: "r1", language: "tr" }, "1-0");
    source.emit(
      "tool_called",
      { tool: "search_documents", summary: "THYAO" },
      "2-0",
    );
    expect(events.map((e) => [e.type, e.id])).toEqual([
      ["run_started", "1-0"],
      ["tool_called", "2-0"],
    ]);
    expect(source.closed).toBe(false);
  });

  it("closes after final", () => {
    const { events, source } = open();
    source.emit("final", { status: "completed", answer: {}, citations: [] });
    expect(events[0]?.type).toBe("final");
    expect(source.closed).toBe(true);
  });

  it("closes after error", () => {
    const { source } = open();
    source.emit("error", { code: "internal_error", message: "x" });
    expect(source.closed).toBe(true);
  });

  it("skips malformed frames", () => {
    const { events, source } = open();
    source.emit("tool_result", "{not json");
    expect(events).toEqual([]);
  });

  it("lets EventSource reconnect, then gives up", () => {
    const { source, onDisconnect } = open();
    for (let i = 0; i < MAX_RECONNECTS; i += 1) source.onerror?.();
    expect(onDisconnect).not.toHaveBeenCalled();
    source.onerror?.();
    expect(onDisconnect).toHaveBeenCalledOnce();
    expect(source.closed).toBe(true);
  });

  it("reports a disconnect when the browser closed the stream", () => {
    const { source, onDisconnect } = open();
    source.readyState = 2;
    source.onerror?.();
    expect(onDisconnect).toHaveBeenCalledOnce();
  });

  it("returns a close function", () => {
    const { close, source, onDisconnect } = open();
    close();
    source.onerror?.();
    expect(source.closed).toBe(true);
    expect(onDisconnect).not.toHaveBeenCalled();
  });
});

describe("api client", () => {
  it("posts the question with cookies and language", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ run_id: "r1" }), { status: 202 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await expect(
      startResearch("THYAO net kâr?", "tr", "http://api.test"),
    ).resolves.toEqual({
      run_id: "r1",
    });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe("http://api.test/v1/research");
    expect(init.credentials).toBe("include");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Accept-Language"]).toBe(
      "tr",
    );
    vi.unstubAllGlobals();
  });

  it("maps the error envelope and Retry-After", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              error: { code: "rate_limited", message: "slow down" },
            }),
            { status: 429, headers: { "Retry-After": "12" } },
          ),
      ),
    );
    const error = await getRun("r1", "en", "http://api.test").catch(
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      code: "rate_limited",
      status: 429,
      retryAfter: 12,
    });
    vi.unstubAllGlobals();
  });

  it("maps non-envelope and network failures", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>", { status: 502 })),
    );
    await expect(getRun("r1", "en", "")).rejects.toMatchObject({
      code: "internal_error",
      status: 502,
      retryAfter: null,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new TypeError("offline"))),
    );
    await expect(getRun("r1", "en", "")).rejects.toMatchObject({
      code: "dependency_unavailable",
    });
    vi.unstubAllGlobals();
  });

  it("builds the stream URL", () => {
    expect(streamUrl("a/b", "http://api.test")).toBe(
      "http://api.test/v1/research/a%2Fb/stream",
    );
  });
});
