import type { Dictionary } from "@/i18n";
import type { Health } from "@/lib/health";

import { FlapRow } from "./FlapRow";

const SHORT_SHA = 7;
const STATE_CELLS = 12;

export function BackendStatus({
  health,
  t,
}: {
  health: Health;
  t: Dictionary;
}) {
  const word = {
    loading: t.boardLoading,
    available: t.boardAvailable,
    unavailable: t.boardUnavailable,
  }[health.state];
  const version =
    health.state === "available" ? health.version.slice(0, SHORT_SHA) : "";

  return (
    <section className="row row--status" aria-labelledby="status-label">
      <h2 id="status-label" className="row__label">
        {t.statusLabel}
      </h2>
      <div className="row__board">
        <FlapRow
          text={word}
          length={STATE_CELLS}
          locale={t.locale}
          tone={health.state === "available" ? "live" : "plain"}
        />
        <FlapRow text={version} length={SHORT_SHA} />
      </div>
      <p
        data-testid="backend-status"
        data-state={health.state}
        role="status"
        aria-live="polite"
        className="row__note"
      >
        {health.state === "loading" && t.statusLoading}
        {health.state === "available" && (
          <>
            {t.statusAvailable} ({t.statusVersion}{" "}
            <code className="sha">{version}</code>)
          </>
        )}
        {health.state === "unavailable" && t.statusUnavailable}
      </p>
    </section>
  );
}
