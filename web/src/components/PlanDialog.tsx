"use client";

import { useId, useState } from "react";

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

function reasonText(reason: PlanReason, t: Dictionary): string {
  if (reason.field === "goal")
    return /advice/i.test(reason.reason)
      ? t.planReasonAdvice
      : t.planReasonGoal;
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

  const move = (index: number, by: number) =>
    setDraft((steps) => {
      if (!steps) return steps;
      const next = [...steps];
      const [step] = next.splice(index, 1);
      next.splice(index + by, 0, step!);
      return next;
    });

  const submitEdit = () => {
    if (!draft || !shown) return;
    if (draft.some((step) => !step.goal.trim())) {
      setError(t.planGoalRequired);
      return;
    }
    void send({ action: "edit", plan: fromDraft(draft, shown) });
  };

  const stepReasons = (id: string) => reasons.filter((r) => r.step === id);
  const planReasons = reasons.filter((r) => r.step === null);
  const estimate = pending.estimate ?? shown?.estimate ?? null;

  return (
    <section
      className="plan"
      aria-labelledby={`${uid}-label`}
      aria-describedby={`${uid}-intro`}
      data-testid="plan-dialog"
      data-kind={offer ? "deep_quota_offer" : "plan"}
    >
      <div className="plan__head">
        <h2 id={`${uid}-label`} className="row__label">
          {t.planLabel}
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
            const field = (name: string) => own.some((r) => r.field === name);
            const base = `${uid}-${step.key}`;
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
                        disabled={index === 0 || busy}
                        onClick={() => move(index, -1)}
                      >
                        <Arrow up />
                      </button>
                      <button
                        type="button"
                        className="key key--icon"
                        aria-label={`${t.planMoveDown}: ${t.planStep} ${index + 1}`}
                        disabled={index === draft.length - 1 || busy}
                        onClick={() => move(index, 1)}
                      >
                        <Arrow up={false} />
                      </button>
                      <button
                        type="button"
                        className="key key--icon"
                        aria-label={`${t.planRemove}: ${t.planStep} ${index + 1}`}
                        disabled={draft.length === 1 || busy}
                        onClick={() =>
                          setDraft(
                            (steps) =>
                              steps?.filter((s) => s.key !== step.key) ?? null,
                          )
                        }
                      >
                        <Cross />
                      </button>
                    </span>
                  </div>
                  <label className="plan-field">
                    <span className="plan-field__label">{t.planGoal}</span>
                    <textarea
                      className="plan-field__input plan-field__input--goal"
                      value={step.goal}
                      maxLength={300}
                      rows={2}
                      aria-invalid={field("goal") || undefined}
                      aria-describedby={
                        field("goal") ? `${base}-goal` : undefined
                      }
                      onChange={(e) =>
                        update(step.key, { goal: e.target.value })
                      }
                    />
                  </label>
                  <div className="plan-step__pair">
                    <label className="plan-field">
                      <span className="plan-field__label">{t.planTickers}</span>
                      <input
                        className="plan-field__input"
                        value={step.tickers}
                        autoCapitalize="characters"
                        spellCheck={false}
                        placeholder={t.planTickersHint}
                        aria-invalid={field("tickers") || undefined}
                        aria-describedby={
                          field("tickers") ? `${base}-tickers` : undefined
                        }
                        onChange={(e) =>
                          update(step.key, { tickers: e.target.value })
                        }
                      />
                    </label>
                    <label className="plan-field">
                      <span className="plan-field__label">{t.planPeriods}</span>
                      <input
                        className="plan-field__input"
                        value={step.periods}
                        autoCapitalize="characters"
                        spellCheck={false}
                        placeholder={t.planPeriodsHint}
                        aria-invalid={field("periods") || undefined}
                        aria-describedby={
                          field("periods") ? `${base}-periods` : undefined
                        }
                        onChange={(e) =>
                          update(step.key, { periods: e.target.value })
                        }
                      />
                    </label>
                  </div>
                  {own.map((reason, i) => (
                    <p
                      key={i}
                      id={`${base}-${reason.field}`}
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
          className="key key--add"
          disabled={draft.length >= PLAN_MAX_STEPS || busy}
          onClick={() =>
            setDraft((steps) =>
              steps
                ? [
                    ...steps,
                    {
                      key: Math.max(-1, ...steps.map((s) => s.key)) + 1,
                      kind: "facts",
                      goal: "",
                      tickers: "",
                      periods: "",
                      metrics: [],
                    },
                  ]
                : steps,
            )
          }
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
              type="button"
              className="key key--primary"
              disabled={busy}
              onClick={submitEdit}
            >
              {busy ? t.planSending : t.planSendEdit}
            </button>
            <button
              type="button"
              className="key"
              disabled={busy}
              onClick={() => {
                setDraft(null);
                setReasons([]);
                setError(null);
              }}
            >
              {t.planDiscard}
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="key key--primary"
              disabled={busy}
              onClick={() => void send({ action: "approve" })}
            >
              {busy ? t.planSending : t.planApprove}
            </button>
            <button
              type="button"
              className="key"
              disabled={busy || !shown}
              onClick={() => shown && setDraft(toDraft(shown))}
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
