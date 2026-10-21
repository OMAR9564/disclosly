"use client";

import { type FormEvent, useId, useState } from "react";

import type { Dictionary } from "@/i18n";

export const QUESTION_MIN = 3;
export const QUESTION_MAX = 1000;

export function AskBox({
  t,
  onAsk,
  busy = false,
  error = null,
}: {
  t: Dictionary;
  onAsk: (question: string) => void;
  busy?: boolean;
  error?: string | null;
}) {
  const id = useId();
  const [question, setQuestion] = useState("");
  const [tooShort, setTooShort] = useState(false);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = question.trim();
    if (trimmed.length < QUESTION_MIN) {
      setTooShort(true);
      return;
    }
    setTooShort(false);
    onAsk(trimmed);
  };

  const message = tooShort ? t.askTooShort : error;

  return (
    <form className="ask" onSubmit={submit} data-testid="ask-box" noValidate>
      <label htmlFor={`${id}-q`} className="row__label">
        {t.askLabel}
      </label>
      <div className="ask__slot">
        <textarea
          id={`${id}-q`}
          className="ask__field"
          value={question}
          maxLength={QUESTION_MAX}
          rows={1}
          placeholder={t.askPlaceholder}
          aria-invalid={message ? true : undefined}
          aria-describedby={`${id}-note`}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              e.currentTarget.form?.requestSubmit();
            }
          }}
        />
        <button type="submit" className="key key--ask" disabled={busy}>
          {busy ? t.askSending : t.askSubmit}
        </button>
      </div>
      <p
        id={`${id}-note`}
        className="ask__note"
        role={message ? "alert" : undefined}
      >
        {message ?? (
          <span className="ask__count">
            {question.length}/{QUESTION_MAX} {t.askCount}
          </span>
        )}
      </p>
    </form>
  );
}
