"use client";

import { type CSSProperties, useEffect, useRef, useState } from "react";

const SPIN_CHARS = "ABCÇDEFGĞHIİJKLMNOÖPRSŞTUÜVYZ0123456789";
const FRAME_MS = 45;
const FRAMES_PER_CELL = 3;

function pad(text: string, length: number, locale: string): string {
  return text.toLocaleUpperCase(locale).padEnd(length, " ").slice(0, length);
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
    : true;
}

/**
 * One row of split-flap tiles. When `text` changes, the tiles spin through characters and
 * settle left to right. Decorative: callers render the same text for assistive technology.
 */
export function FlapRow({
  text,
  length,
  tone = "plain",
  locale = "en",
}: {
  text: string;
  length: number;
  tone?: "plain" | "live";
  /** Upper-casing locale: "tr" maps i to İ, which the brand name must not get. */
  locale?: string;
}) {
  const target = pad(text, length, locale);
  const [shown, setShown] = useState(target);
  const frame = useRef(0);

  useEffect(() => {
    if (prefersReducedMotion()) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- jump straight to the new text
      setShown(target);
      return;
    }
    frame.current = 0;
    const timer = window.setInterval(() => {
      frame.current += 1;
      const settled = Math.floor(frame.current / FRAMES_PER_CELL);
      setShown(
        Array.from(target, (char, i) =>
          i < settled || char === " "
            ? char
            : SPIN_CHARS[(i * 7 + frame.current) % SPIN_CHARS.length],
        ).join(""),
      );
      if (settled >= length) window.clearInterval(timer);
    }, FRAME_MS);
    return () => window.clearInterval(timer);
  }, [target, length]);

  return (
    <span
      aria-hidden="true"
      className={`flaps flaps--${tone}`}
      style={{ "--n": length } as CSSProperties}
    >
      {Array.from(shown, (char, i) => (
        <span key={i} className={char === " " ? "flap flap--blank" : "flap"}>
          {char}
        </span>
      ))}
    </span>
  );
}
