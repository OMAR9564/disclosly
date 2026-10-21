"use client";

import { useEffect, useRef } from "react";

const SCRIPT_URL =
  "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

interface TurnstileApi {
  render: (
    element: HTMLElement,
    options: {
      sitekey: string;
      theme: "dark";
      language: string;
      callback: (token: string) => void;
      "expired-callback": () => void;
      "error-callback": () => void;
    },
  ) => string;
  remove: (widgetId: string) => void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

let loading: Promise<TurnstileApi> | null = null;

function loadTurnstile(): Promise<TurnstileApi> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  loading ??= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = SCRIPT_URL;
    script.async = true;
    script.onload = () =>
      window.turnstile
        ? resolve(window.turnstile)
        : reject(new Error("turnstile"));
    script.onerror = () => {
      loading = null;
      reject(new Error("turnstile"));
    };
    document.head.appendChild(script);
  });
  return loading;
}

/** Cloudflare Turnstile widget; reports the token, or `null` when it expires or fails. */
export function Turnstile({
  siteKey,
  language,
  onToken,
  onError,
}: {
  siteKey: string;
  language: string;
  onToken: (token: string | null) => void;
  onError: () => void;
}) {
  const slot = useRef<HTMLDivElement>(null);
  const handlers = useRef({ onToken, onError });
  useEffect(() => {
    handlers.current = { onToken, onError };
  });

  useEffect(() => {
    let widget: string | null = null;
    let active = true;
    loadTurnstile()
      .then((api) => {
        if (!active || !slot.current) return;
        widget = api.render(slot.current, {
          sitekey: siteKey,
          theme: "dark",
          language,
          callback: (token) => handlers.current.onToken(token),
          "expired-callback": () => handlers.current.onToken(null),
          "error-callback": () => handlers.current.onError(),
        });
      })
      .catch(() => {
        if (active) handlers.current.onError();
      });
    return () => {
      active = false;
      if (widget && window.turnstile) window.turnstile.remove(widget);
    };
  }, [siteKey, language]);

  return <div ref={slot} className="turnstile" data-testid="turnstile" />;
}
