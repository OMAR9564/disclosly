import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RunView, runIdFromPath } from "@/components/RunView";
import { tr } from "@/i18n/tr";

import { routeParams } from "../unit/setup";
import { ANSWER, CITATIONS } from "./fixtures";

class FakeSource {
  static last: FakeSource;
  listeners = new Map<string, (e: MessageEvent) => void>();
  onerror: (() => void) | null = null;
  readyState = 1;
  constructor(readonly url: string) {
    FakeSource.last = this;
  }
  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    this.listeners.set(type, fn);
  }
  close() {
    this.readyState = 2;
  }
  emit(type: string, data: unknown) {
    act(() =>
      this.listeners.get(type)?.(
        new MessageEvent(type, {
          data: JSON.stringify(data),
          lastEventId: "1-0",
        }),
      ),
    );
  }
}

function runBody(status = 200) {
  return status === 200
    ? JSON.stringify({
        run_id: "run-1",
        question: "THYAO net kâr?",
        status: "running",
      })
    : JSON.stringify({ error: { code: "not_found", message: "x" } });
}

beforeEach(() => {
  routeParams.id = "run-1";
  vi.stubGlobal("EventSource", FakeSource);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(runBody(), { status: 200 })),
  );
});

afterEach(() => vi.unstubAllGlobals());

describe("RunView", () => {
  it("shows the question, follows the stream and renders the answer", async () => {
    render(<RunView />);
    await waitFor(() =>
      expect(screen.getByTestId("question").textContent).toBe("THYAO net kâr?"),
    );
    expect(FakeSource.last.url).toContain("/v1/research/run-1/stream");
    FakeSource.last.emit("run_started", { run_id: "run-1", language: "tr" });
    FakeSource.last.emit("final", {
      status: "completed",
      answer: ANSWER,
      citations: CITATIONS,
    });
    expect(screen.getByTestId("answer")).toBeTruthy();
    expect(screen.getByTestId("disclaimer")).toBeTruthy();
    expect(FakeSource.last.readyState).toBe(2);
  });

  it("shows a stream error", () => {
    render(<RunView />);
    FakeSource.last.emit("error", {
      code: "providers_unavailable",
      message: "x",
    });
    expect(screen.getByTestId("run-error").textContent).toContain(
      tr.errorProviders,
    );
  });

  it("shows not found for someone else's run", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(runBody(404), { status: 404 })),
    );
    render(<RunView />);
    await waitFor(() =>
      expect(screen.getByTestId("run-error").textContent).toContain(
        tr.errorNotFound,
      ),
    );
    expect(screen.queryByTestId("timeline")).toBeNull();
  });

  it("tells the user when the connection is lost", () => {
    render(<RunView />);
    for (let i = 0; i < 6; i += 1) act(() => FakeSource.last.onerror?.());
    expect(screen.getByText(tr.runLost)).toBeTruthy();
  });
});

describe("runIdFromPath", () => {
  it.each([
    [
      "/runs/f6cc8636-5ec5-451e-bf65-26143e0afd51",
      "f6cc8636-5ec5-451e-bf65-26143e0afd51",
    ],
    ["/runs/abc/", "abc"],
    ["/runs/a%20b", "a b"],
    ["/runs/%E0%A4%A", "%E0%A4%A"],
    [null, ""],
  ])("%s -> %s", (path, id) => {
    expect(runIdFromPath(path)).toBe(id);
  });
});
