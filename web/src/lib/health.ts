export const HEALTH_TIMEOUT_MS = 5000;

export type Health =
  | { state: "loading" }
  | { state: "available"; version: string }
  | { state: "unavailable" };

export type HealthResult = Exclude<Health, { state: "loading" }>;

const UNAVAILABLE: HealthResult = { state: "unavailable" };

function isHealthBody(
  body: unknown,
): body is { status: "ok"; version: string; service: string } {
  if (typeof body !== "object" || body === null || Array.isArray(body))
    return false;
  const record = body as Record<string, unknown>;
  return (
    record.status === "ok" &&
    typeof record.version === "string" &&
    record.version.length > 0 &&
    record.service === "disclosly-api"
  );
}

/** Never throws: any failure (non-200, bad body, network, timeout) is "unavailable". */
export async function fetchHealth(
  baseUrl: string = process.env.NEXT_PUBLIC_API_BASE_URL ?? "",
): Promise<HealthResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), HEALTH_TIMEOUT_MS);
  try {
    const response = await fetch(`${baseUrl}/healthz`, {
      signal: controller.signal,
      cache: "no-store",
    });
    if (response.status !== 200) return UNAVAILABLE;
    const body: unknown = await response.json();
    return isHealthBody(body)
      ? { state: "available", version: body.version }
      : UNAVAILABLE;
  } catch {
    return UNAVAILABLE;
  } finally {
    clearTimeout(timer);
  }
}
