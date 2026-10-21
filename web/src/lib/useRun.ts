"use client";

import { useEffect, useReducer, useState } from "react";

import type { Language } from "@/i18n";
import { ApiError, type Run, getRun, streamUrl } from "@/lib/api";
import { type RunState, initialRunState, reduceRun } from "@/lib/runState";
import { type RunEvent, subscribeRun } from "@/lib/stream";

/** Loads a run and follows its event stream; replays the full history on reload. */
export function useRun(
  runId: string,
  language: Language,
): {
  run: Run | null;
  state: RunState;
  lost: boolean;
  loadError: string | null;
} {
  const [state, dispatch] = useReducer(
    (current: RunState, event: RunEvent | "reset") =>
      event === "reset" ? initialRunState : reduceRun(current, event),
    initialRunState,
  );
  const [run, setRun] = useState<Run | null>(null);
  const [lost, setLost] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    dispatch("reset");
    getRun(runId, language)
      .then((loaded) => {
        if (active) setRun(loaded);
      })
      .catch((error: unknown) => {
        if (active)
          setLoadError(
            error instanceof ApiError ? error.code : "internal_error",
          );
      });
    const close = subscribeRun(streamUrl(runId), {
      onEvent: (event) => dispatch(event),
      onDisconnect: () => setLost(true),
    });
    return () => {
      active = false;
      close();
    };
    // Language changes only re-label the board; they must not restart the run.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  return { run, state, lost, loadError };
}
