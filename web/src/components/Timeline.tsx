import { type Dictionary, isLanguage } from "@/i18n";
import { humanize } from "@/i18n/labels";
import { type RunState, STAGES, type Stage } from "@/lib/runState";

import { FlapRow } from "./FlapRow";

const CELL_TILES = 4;

function checkWord(state: RunState, t: Dictionary): string {
  if (state.phase === "error") return t.boardError;
  if (state.phase === "cancelled") return t.checkCancelled;
  if (state.phase === "expired") return t.checkExpired;
  switch (state.status) {
    case "completed":
      return t.checkCompleted;
    case "partial":
      return t.checkPartial;
    case "refused":
      return t.checkRefused;
    case "no_evidence":
      return t.checkNoEvidence;
    default:
      return "—";
  }
}

function cellValue(stage: Stage, state: RunState, t: Dictionary): string {
  if (stage === "check") return checkWord(state, t);
  const count = state.counts[stage];
  return count === 0 && state.active !== stage ? "—" : String(count);
}

/** The annunciator: four stage cells that flip as run events arrive, plus a plain step log. */
export function Timeline({ state, t }: { state: RunState; t: Dictionary }) {
  const labels: Record<Stage, string> = {
    search: t.stageSearch,
    facts: t.stageFacts,
    model: t.stageModel,
    check: t.stageCheck,
  };
  const note =
    state.phase === "connecting"
      ? t.runConnecting
      : state.phase === "running"
        ? t.runRunning
        : state.phase === "awaiting"
          ? t.runAwaiting
          : state.phase === "cancelled"
            ? t.runCancelled
            : state.phase === "expired"
              ? t.runExpired
              : state.phase === "done"
                ? t.runDone
                : null;

  return (
    <section
      className="row"
      aria-labelledby="stages-label"
      data-testid="timeline"
    >
      <h2 id="stages-label" className="sr-only">
        {t.stagesLabel}
      </h2>
      <ol className="stages">
        {STAGES.map((stage) => {
          const value = cellValue(stage, state, t);
          const live =
            state.active === stage &&
            (stage !== "check" || state.status === "completed");
          return (
            <li
              key={stage}
              className="stage"
              data-stage={stage}
              data-live={live}
            >
              <span className="stage__name">{labels[stage]}</span>
              <FlapRow
                text={value}
                length={stage === "check" ? 7 : CELL_TILES}
                locale={t.locale}
                tone={live ? "live" : "plain"}
              />
              <span className="sr-only">
                {labels[stage]}: {value}
              </span>
            </li>
          );
        })}
      </ol>
      {state.steps.length > 0 && (
        <details
          className="steps-box"
          open={state.phase === "running" || state.phase === "connecting"}
        >
          <summary className="key key--steps">
            {state.steps.length} {t.stepsCount}
          </summary>
          <ol className="steps" aria-label={t.stagesLabel}>
            {state.steps.map((step) => (
              <li key={step.id} className="steps__item">
                <span className="steps__what">
                  {humanize(
                    step.summary,
                    isLanguage(t.locale) ? t.locale : "tr",
                  )}
                </span>
                {step.status && step.status !== "pending" && (
                  <span className="steps__count" data-status={step.status}>
                    {
                      {
                        done: t.stepDone,
                        not_covered: t.stepNotCovered,
                        failed: t.stepFailed,
                      }[step.status]
                    }
                  </span>
                )}
                {step.count !== null && (
                  <span className="steps__count">
                    {step.count} {t.stepResults}
                  </span>
                )}
              </li>
            ))}
          </ol>
        </details>
      )}
      {note && (
        <p className="row__note" role="status" aria-live="polite">
          {note}
        </p>
      )}
    </section>
  );
}
