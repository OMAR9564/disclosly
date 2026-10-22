import { FlapRow } from "@/components/FlapRow";
import type { Dictionary } from "@/i18n";
import type { QuotaStatus, Quotas } from "@/lib/api";

/** "00:00 (TSİ)": the reset time in Istanbul, where the quota day turns. */
export function resetLabel(resetsAt: string, t: Dictionary): string {
  const time = new Intl.DateTimeFormat(t.locale, {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Europe/Istanbul",
  }).format(new Date(resetsAt));
  return `${time} (${t.quotaZone})`;
}

export function quotaExhaustedMessage(
  quota: QuotaStatus,
  t: Dictionary,
): string {
  return t.quotaExhausted.replace("{time}", resetLabel(quota.resets_at, t));
}

function Counter({
  quota,
  label,
  left,
  testId,
}: {
  quota: QuotaStatus;
  label: string;
  left: string;
  testId: string;
}) {
  const remaining = Math.max(0, Math.min(quota.remaining, 99));
  return (
    <p className="quota" data-testid={testId}>
      <span className="quota__label" aria-hidden="true">
        {label}
      </span>
      <span className="quota__tiles">
        <FlapRow
          text={String(remaining).padStart(2, "0")}
          length={2}
          align="end"
        />
      </span>
      <span className="quota__limit" aria-hidden="true">
        /{quota.limit}
      </span>
      <span className="sr-only">
        {remaining} / {quota.limit} {left}
      </span>
    </p>
  );
}

/** Questions and deep research runs left today, as flap tiles in the top strip. */
export function QuotaBadge({ quota, t }: { quota: Quotas; t: Dictionary }) {
  return (
    <div className="quotas">
      <Counter
        quota={quota.simple}
        label={t.quotaLabel}
        left={t.quotaLeft}
        testId="quota-badge"
      />
      <Counter
        quota={quota.deep}
        label={t.quotaDeepLabel}
        left={t.quotaDeepLeft}
        testId="quota-deep"
      />
    </div>
  );
}
