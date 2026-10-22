"use client";

import { useEffect, useId, useRef, useState } from "react";

import type { Dictionary } from "@/i18n";
import {
  ApiError,
  type Decision,
  type DecisionAccepted,
  type Plan,
  type PlanReason,
  type StepKind,
} from "@/lib/api";
import { errorMessage } from "@/lib/messages";
import type { PendingDecision } from "@/lib/runState";

import { FlapRow } from "./FlapRow";

/**
 * The decision a deep run waits on (research §R4, §R12): the proposed plan with its estimate
 * and the deep runs left, to approve, edit or cancel; or, when no deep run is left today, the
 * offer to answer in simple mode. It sits on the board under the annunciator, not in a modal:
 * the run is paused, nothing else on the page competes for the decision.
 */

export const PLAN_MAX_STEPS = 8;
const KINDS: StepKind[] = ["facts", "search", "calculate"];

interface DraftStep {
  key: number;
  kind: StepKind;
  goal: string;
  tickers: string;
  periods: string;
  metrics: string[];
}

function toDraft(plan: Plan): DraftStep[] {
  return plan.steps.map((step, index) => ({
    key: index,
    kind: step.kind,
    goal: step.goal,
    tickers: step.tickers.join(", "),
    periods: step.periods.join(", "),
    metrics: step.metrics,
  }));
}

function split(value: string, upper: boolean): string[] {
  const items = value
    .split(/[\s,;]+/)
    .map((item) => (upper ? item.toLocaleUpperCase("en") : item))
    .filter(Boolean);
  return [...new Set(items)];
}

/** The edited plan as the API takes it: ids follow the order shown. */
export function fromDraft(steps: DraftStep[], base: Plan): Plan {
  return {
    steps: steps.map((step, index) => ({
      id: `s${index + 1}`,
      kind: step.kind,
      goal: step.goal.trim(),
      tickers: split(step.tickers, true),
      periods: split(step.periods, true),
      metrics: step.metrics,
    })),
    edited: true,
    revision: base.revision ?? 0,
  };
}

/** Fields whose reasons sit right under their own input. */
const FIELDS = ["goal", "tickers", "periods"] as const;
const GOAL_REQUIRED = "required";

function reasonText(reason: PlanReason, t: Dictionary): string {
  if (reason.field === "goal") {
    if (reason.reason === GOAL_REQUIRED) return t.planGoalRequired;
    return /advice/i.test(reason.reason)
      ? t.planReasonAdvice
      : t.planReasonGoal;
  }
  if (reason.field === "tickers") return t.planReasonTickers;
  if (reason.field === "periods") return t.planReasonPeriods;
  if (reason.field === "estimate") return t.planReasonEstimate;
  if (reason.field === "steps")
    return t.planMaxSteps.replace("{max}", String(PLAN_MAX_STEPS));
  return t.planInvalid;
}

function timeLabel(iso: string, t: Dictionary): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat(t.locale, {
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Europe/Istanbul",
  }).format(date);
}

function Arrow({ up }: { up: boolean }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d={up ? "M3 9l4-4 4 4" : "M3 5l4 4 4-4"}
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="square"
      />
    </svg>
  );
}

function Cross() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M3.5 3.5l7 7M10.5 3.5l-7 7"
        stroke="currentColor"
        strokeWidth="2"
      />
    </svg>
  );
}

function kindLabel(kind: StepKind, t: Dictionary): string {
  return {
    facts: t.kindFacts,
    search: t.kindSearch,
    calculate: t.kindCalculate,
  }[kind];
}

export function PlanDialog({
  pending,
  plan,
  decide,
  onDecided,
  t,
}: {
  pending: PendingDecision;
  /** The latest proposed plan (the pending one, or the stream's last `plan_proposed`). */
  plan: Plan | null;
  decide: (decision: Decision) => Promise<DecisionAccepted>;
  onDecided: (accepted: DecisionAccepted) => void;
  t: Dictionary;
}) {
  const uid = useId();
  const shown = pending.plan ?? plan;
  const [draft, setDraft] = useState<DraftStep[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reasons, setReasons] = useState<PlanReason[]>([]);
  const offer = pending.kind === "deep_quota_offer" || !shown;
  const editing = draft !== null;
  // Focus follows a step that moved or a key that vanished: handlers name the next target
  // (`data-focus` on keys and fields), and the effect after that render moves focus there.
  const section = useRef<HTMLElement>(null);
  const focusNext = useRef<string | null>(null);
  useEffect(() => {
    const target = focusNext.current;
    if (target === null) return;
    focusNext.current = null;
    section.current
      ?.querySelector<HTMLElement>(`[data-focus="${target}"]`)
      ?.focus();
  });

  const send = async (decision: Decision) => {
    setBusy(true);
    setError(null);
    setReasons([]);
    try {
      onDecided(await decide(decision));
    } catch (caught) {
      const code = caught instanceof ApiError ? caught.code : "internal_error";
      if (caught instanceof ApiError && code === "plan_invalid")
        setReasons(caught.reasons);
      setError(
        code === "quota_exhausted" ? t.errorDeepQuota : errorMessage(code, t),
      );
      setBusy(false);
    }
  };

  const update = (key: number, patch: Partial<DraftStep>) =>
    setDraft(
      (steps) =>
        steps?.map((step) =>
          step.key === key ? { ...step, ...patch } : step,
        ) ?? null,
    );

  const move = (index: number, by: number) => {
    if (!draft) return;
    const next = [...draft];
    const [step] = next.splice(index, 1);
    next.splice(index + by, 0, step!);
    const to = index + by;
    // At an edge the pressed arrow is disabled: hand focus to the other one.
    const edge = by < 0 ? to === 0 : to === next.length - 1;
    focusNext.current = `${step!.key}-${by < 0 !== edge ? "up" : "down"}`;
    setDraft(next);
  };

  const remove = (index: number) => {
    if (!draft) return;
    const next = draft.filter((_, i) => i !== index);
    const after = next[index];
    focusNext.current = after ? `${after.key}-remove` : "add";
    setDraft(next);
  };

  const add = () => {
    if (!draft) return;
    const key = Math.max(-1, ...draft.map((s) => s.key)) + 1;
    focusNext.current = `${key}-goal`;
    setDraft([
      ...draft,
      { key, kind: "facts", goal: "", tickers: "", periods: "", metrics: [] },
    ]);
  };

  const startEditing = () => {
    if (!shown) return;
    const steps = toDraft(shown);
    focusNext.current = `${steps[0]!.key}-kind`;
    setDraft(steps);
  };

  const discard = () => {
    focusNext.current = "edit";
    setDraft(null);
    setReasons([]);
    setError(null);
  };

  const submitEdit = () => {
    if (!draft || !shown) return;
    const empty = draft.flatMap((step, i) =>
      step.goal.trim()
        ? []
        : [{ step: `s${i + 1}`, field: "goal", reason: GOAL_REQUIRED }],
    );
    if (empty.length > 0) {
      setReasons(empty);
      setError(t.planGoalRequired);
      focusNext.current = `${draft[Number(empty[0]!.step.slice(1)) - 1]!.key}-goal`;
      return;
    }
    void send({ action: "edit", plan: fromDraft(draft, shown) });
  };

  const stepReasons = (id: string) => reasons.filter((r) => r.step === id);
  const planReasons = reasons.filter((r) => r.step === null);
  const estimate = pending.estimate ?? shown?.estimate ?? null;

  return (
    <section
      ref={section}
      className="plan"
      aria-labelledby={`${uid}-label`}
      aria-describedby={`${uid}-intro`}
      data-testid="plan-dialog"
      data-kind={offer ? "deep_quota_offer" : "plan"}
    >
      <div className="plan__head">
        <h2 id={`${uid}-label`} className="row__label">
          {t.planLabel}
          <span className="sr-only">
            : {offer ? t.offerBoard : t.planBoard}
          </span>
        </h2>
        <span className="plan__board">
          <FlapRow
            text={offer ? t.offerBoard : t.planBoard}
            length={9}
            locale={t.locale}
            tone="live"
          />
        </span>
      </div>

      <p id={`${uid}-intro`} className="plan__intro">
        {offer ? t.offerIntro : t.planIntro}{" "}
        {t.planValidUntil.replace("{time}", timeLabel(pending.expiresAt, t))}
      </p>

      {!offer && (
        <dl className="plan__meta">
          {estimate && (
            <div>
              <dt>{t.planEstimate}</dt>
              <dd>
                {t.planEstimateValue
                  .replace("{typical}", String(estimate.typical))
                  .replace("{max}", String(estimate.max))}
              </dd>
            </div>
          )}
          <div>
            <dt>{t.planDeepLeft}</dt>
            <dd data-testid="plan-deep-left">{pending.deepRemaining}</dd>
          </div>
        </dl>
      )}

      {!offer && shown && !editing && (
        <ol className="plan__steps" aria-label={t.planLabel}>
          {shown.steps.map((step, index) => (
            <li key={step.id} className="plan-step" data-kind={step.kind}>
              <span className="plan-step__no" aria-hidden="true">
                <FlapRow text={String(index + 1)} length={1} />
              </span>
              <div className="plan-step__body">
                <p className="plan-step__goal">
                  <span className="sr-only">
                    {t.planStep} {index + 1}.{" "}
                  </span>
                  {step.goal}
                </p>
                <p className="plan-step__tags">
                  <span className="plan-step__kind">
                    {kindLabel(step.kind, t)}
                  </span>
                  {step.tickers.map((ticker) => (
                    <span key={ticker} className="plan-step__ticker">
                      {ticker}
                    </span>
                  ))}
                  {step.periods.length > 0 && (
                    <span className="plan-step__periods">
                      {step.periods.join(" · ")}
                    </span>
                  )}
                </p>
              </div>
            </li>
          ))}
        </ol>
      )}

      {editing && (
        <ol className="plan__steps plan__steps--edit" aria-label={t.planLabel}>
          {draft.map((step, index) => {
            const id = `s${index + 1}`;
            const own = stepReasons(id);
            const base = `${uid}-${step.key}`;
            const ofField = (name: string) =>
              own.flatMap((r, i) =>
                r.field === name
                  ? [{ reason: r, id: `${base}-${name}-${i}` }]
                  : [],
              );
            const invalid = (name: string) =>
              ofField(name).length > 0 || undefined;
            const describedBy = (name: string) =>
              ofField(name)
                .map((r) => r.id)
                .join(" ") || undefined;
            const notes = (name: string) =>
              ofField(name).map(({ reason, id: noteId }) => (
                <span
                  key={noteId}
                  id={noteId}
                  className="plan-step__reason"
                  data-testid="plan-reason"
                >
                  {reasonText(reason, t)}
                </span>
              ));
            const other = own.filter(
              (r) => !(FIELDS as readonly string[]).includes(r.field),
            );
            return (
              <li
                key={step.key}
                className="plan-step plan-step--edit"
                data-testid="plan-step"
              >
                <span className="plan-step__no" aria-hidden="true">
                  <FlapRow text={String(index + 1)} length={1} />
                </span>
                <fieldset className="plan-step__body">
                  <legend className="sr-only">
                    {t.planStep} {index + 1}
                  </legend>
                  <div className="plan-step__bar">
                    <label className="plan-field plan-field--kind">
                      <span className="plan-field__label">{t.planKind}</span>
                      <select
                        data-focus={`${step.key}-kind`}
                        className="plan-field__input"
                        value={step.kind}
                        onChange={(e) =>
                          update(step.key, { kind: e.target.value as StepKind })
                        }
                      >
                        {KINDS.map((kind) => (
                          <option key={kind} value={kind}>
                            {kindLabel(kind, t)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <span className="plan-step__keys">
                      <button
                        type="button"
                        className="key key--icon"
                        aria-label={`${t.planMoveUp}: ${t.planStep} ${index + 1}`}
                        data-focus={`${step.key}-up`}
                        disabled={index === 0 || busy}
                        onClick={() => move(index, -1)}
                      >
                        <Arrow up />
                      </button>
                      <button
                        type="button"
                        className="key key--icon"
                        aria-label={`${t.planMoveDown}: ${t.planStep} ${index + 1}`}
                        data-focus={`${step.key}-down`}
                        disabled={index === draft.length - 1 || busy}
                        onClick={() => move(index, 1)}
                      >
                        <Arrow up={false} />
                      </button>
                      <button
                        type="button"
                        className="key key--icon"
                        aria-label={`${t.planRemove}: ${t.planStep} ${index + 1}`}
                        data-focus={`${step.key}-remove`}
                        disabled={draft.length === 1 || busy}
                        onClick={() => remove(index)}
                      >
                        <Cross />
                      </button>
                    </span>
                  </div>
                  <div className="plan-field">
                    <label
                      className="plan-field__label"
                      htmlFor={`${base}-goal`}
                    >
                      {t.planGoal}
                    </label>
                    <textarea
                      id={`${base}-goal`}
                      data-focus={`${step.key}-goal`}
                      className="plan-field__input plan-field__input--goal"
                      value={step.goal}
                      maxLength={300}
                      rows={2}
                      aria-invalid={invalid("goal")}
                      aria-describedby={describedBy("goal")}
                      onChange={(e) =>
                        update(step.key, { goal: e.target.value })
                      }
                    />
                    {notes("goal")}
                  </div>
                  <div className="plan-step__pair">
                    <div className="plan-field">
                      <label
                        className="plan-field__label"
                        htmlFor={`${base}-tickers`}
                      >
                        {t.planTickers}
                      </label>
                      <input
                        id={`${base}-tickers`}
                        className="plan-field__input"
                        value={step.tickers}
                        autoCapitalize="characters"
                        spellCheck={false}
                        placeholder={t.planTickersHint}
                        aria-invalid={invalid("tickers")}
                        aria-describedby={describedBy("tickers")}
                        onChange={(e) =>
                          update(step.key, { tickers: e.target.value })
                        }
                      />
                      {notes("tickers")}
                    </div>
                    <div className="plan-field">
                      <label
                        className="plan-field__label"
                        htmlFor={`${base}-periods`}
                      >
                        {t.planPeriods}
                      </label>
                      <input
                        id={`${base}-periods`}
                        className="plan-field__input"
                        value={step.periods}
                        autoCapitalize="characters"
                        spellCheck={false}
                        placeholder={t.planPeriodsHint}
                        aria-invalid={invalid("periods")}
                        aria-describedby={describedBy("periods")}
                        onChange={(e) =>
                          update(step.key, { periods: e.target.value })
                        }
                      />
                      {notes("periods")}
                    </div>
                  </div>
                  {other.map((reason, i) => (
                    <p
                      key={i}
                      className="plan-step__reason"
                      data-testid="plan-reason"
                    >
                      {reasonText(reason, t)}
                    </p>
                  ))}
                </fieldset>
              </li>
            );
          })}
        </ol>
      )}

      {editing && (
        <button
          type="button"
          data-focus="add"
          className="key key--add"
          disabled={draft.length >= PLAN_MAX_STEPS || busy}
          onClick={add}
        >
          {t.planAddStep}
        </button>
      )}
      {editing && draft.length >= PLAN_MAX_STEPS && (
        <p className="plan__hint">
          {t.planMaxSteps.replace("{max}", String(PLAN_MAX_STEPS))}
        </p>
      )}

      {(error || planReasons.length > 0) && (
        <div className="plan__error" role="alert" data-testid="plan-error">
          {error && <p>{error}</p>}
          {planReasons.map((reason, i) => (
            <p key={i}>{reasonText(reason, t)}</p>
          ))}
        </div>
      )}

      <div className="plan__keys">
        {offer ? (
          <button
            type="button"
            className="key key--primary"
            disabled={busy}
            onClick={() => void send({ action: "simple" })}
          >
            {busy ? t.planSending : t.offerSimple}
          </button>
        ) : editing ? (
          <>
            <button
              key="send-edit"
              type="button"
              className="key key--primary"
              disabled={busy}
              onClick={submitEdit}
            >
              {busy ? t.planSending : t.planSendEdit}
            </button>
            <button
              key="discard"
              type="button"
              className="key"
              disabled={busy}
              onClick={discard}
            >
              {t.planDiscard}
            </button>
          </>
        ) : (
          <>
            <button
              key="approve"
              type="button"
              className="key key--primary"
              disabled={busy}
              onClick={() => void send({ action: "approve" })}
            >
              {busy ? t.planSending : t.planApprove}
            </button>
            <button
              key="edit"
              data-focus="edit"
              type="button"
              className="key"
              disabled={busy || !shown}
              onClick={startEditing}
            >
              {t.planEdit}
            </button>
          </>
        )}
        <button
          type="button"
          className="key key--quiet"
          disabled={busy}
          onClick={() => void send({ action: "cancel" })}
        >
          {t.planCancel}
        </button>
      </div>
    </section>
  );
}
