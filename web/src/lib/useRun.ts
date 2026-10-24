"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import type { Language } from "@/i18n";
import { ApiError, type Run, getRun, streamUrl } from "@/lib/api";
import { type RunState, initialRunState, reduceRun } from "@/lib/runState";
import { type RunEvent, reconnectDelay, subscribeRun } from "@/lib/stream";

/**
 * Loads a run and follows its event stream; replays the full history on reload. The server
 * closes the stream when the run pauses for a decision; `follow()` opens it again after the
 * decision and skips the events already shown. A lost stream (the API restarting during a
 * deploy) is opened again with growing waits until events arrive.
 */
export function useRun(
  runId: string,
  language: Language,
): {
  run: Run | null;
  state: RunState;
  lost: boolean;
  loadError: string | null;
  follow: () => void;
} {
  const [state, dispatch] = useReducer(
    (current: RunState, event: RunEvent | "reset") =>
      event === "reset" ? initialRunState : reduceRun(current, event),
    initialRunState,
  );
  const [run, setRun] = useState<Run | null>(null);
  const [lost, setLost] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const lastId = useRef<string | null>(null);
  const close = useRef<(() => void) | null>(null);
  const retries = useRef(0);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    lastId.current = state.lastId;
  }, [state.lastId]);

  const subscribe = useCallback(
    function open(after: string | null) {
      close.current?.();
      if (retryTimer.current) clearTimeout(retryTimer.current);
      close.current = subscribeRun(streamUrl(runId), {
        after,
        onEvent: (event) => {
          retries.current = 0;
          setLost(false);
          dispatch(event);
        },
        onDisconnect: () => {
          setLost(true);
          retryTimer.current = setTimeout(
            () => open(lastId.current),
            reconnectDelay(retries.current++),
          );
        },
      });
    },
    [runId],
  );

  useEffect(() => {
    let active = true;
    dispatch("reset");
    lastId.current = null;
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
    retries.current = 0;
    subscribe(null);
    return () => {
      active = false;
      if (retryTimer.current) clearTimeout(retryTimer.current);
      close.current?.();
      close.current = null;
    };
    // Language changes only re-label the board; they must not restart the run.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  const follow = useCallback(() => {
    setLost(false);
    subscribe(lastId.current);
  }, [subscribe]);

  return { run, state, lost, loadError, follow };
}
