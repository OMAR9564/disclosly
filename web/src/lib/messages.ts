import type { Dictionary } from "@/i18n";

/** User-facing text for an API or stream error code; never the raw error. */
export function errorMessage(
  code: string | null | undefined,
  t: Dictionary,
): string {
  switch (code) {
    case "unauthorized":
      return t.errorUnauthorized;
    case "beta_closed":
      return t.errorBetaClosed;
    case "quota_exhausted":
      return t.errorQuota;
    case "rate_limited":
      return t.errorRateLimited;
    case "providers_unavailable":
      return t.errorProviders;
    case "not_found":
      return t.errorNotFound;
    case "plan_expired":
      return t.errorPlanExpired;
    case "already_decided":
      return t.errorAlreadyDecided;
    case "not_awaiting_decision":
      return t.errorNotAwaiting;
    case "plan_invalid":
      return t.planInvalid;
    case "sandbox_unavailable":
      return t.errorSandbox;
    case "interrupted_limit":
      return t.errorInterrupted;
    default:
      return t.errorGeneric;
  }
}
