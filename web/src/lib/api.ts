import type { Language } from "@/i18n";

/** Types and calls for the research API (contracts/http-api.md, agent-answer.md). */

export type RunStatus =
  | "running"
  | "awaiting_approval"
  | "completed"
  | "partial"
  | "refused"
  | "no_evidence"
  | "failed"
  | "cancelled"
  | "expired";

export type AnswerStatus = "completed" | "partial" | "refused" | "no_evidence";

export type ErrorCode =
  | "not_found"
  | "method_not_allowed"
  | "validation_error"
  | "dependency_unavailable"
  | "internal_error"
  | "unauthorized"
  | "beta_closed"
  | "bot_check_failed"
  | "quota_exhausted"
  | "rate_limited"
  | "providers_unavailable"
  | "already_decided"
  | "not_awaiting_decision"
  | "plan_expired"
  | "plan_invalid"
  | "watchlist_full"
  | "company_not_covered"
  | "sandbox_unavailable"
  | "interrupted_limit";

export interface Point {
  text: string;
  cite_ids: string[];
}

export interface CitationRef {
  marker: number;
  cite_id: string;
}

export interface Cell {
  text: string;
  cite_id?: string | null;
}

export interface Table {
  title: string;
  columns: string[];
  rows: Cell[][];
}

export interface Chart {
  kind: "line" | "bar";
  table: number;
  x_column: string;
  series: string[];
}

export interface Confidence {
  level: "high" | "medium" | "low";
  note: string;
}

/** The final output of both modes (contracts/report.md); a Phase 3 answer is a Report with
 * the new fields missing, so they are optional here and read through `asReport`. */
export interface Report {
  language: Language;
  status: AnswerStatus;
  summary: string;
  points: Point[];
  tables?: Table[];
  chart?: Chart | null;
  confidence?: Confidence | null;
  unverified?: string[];
  citations: CitationRef[];
  disclaimer: string;
}

/** Kept for the Phase 3 components and runs stored before Phase 4. */
export type Answer = Report;

export interface DocumentSource {
  kind: "chunk" | "fact";
  document_id: number;
  ticker: string;
  doc_type: string;
  period: string;
  section?: string;
  page?: number | null;
  metric?: string;
  source_url: string;
}

export interface CalculationInput {
  name: string;
  cite_id: string;
  value: string;
}

export interface CalculationSource {
  kind: "calculation";
  name: string;
  inputs: CalculationInput[];
  code: string;
  output: string;
}

export type CitationSource = DocumentSource | CalculationSource;

export interface Citation {
  cite_id: string;
  excerpt: string;
  source: CitationSource;
}

export function isCalculation(
  citation: Citation,
): citation is Citation & { source: CalculationSource } {
  return citation.source.kind === "calculation";
}

export type StepKind = "facts" | "search" | "calculate";
export type StepStatus = "pending" | "done" | "not_covered" | "failed";

export interface PlanStep {
  id: string;
  kind: StepKind;
  goal: string;
  tickers: string[];
  periods: string[];
  metrics: string[];
  status?: StepStatus;
}

export interface Estimate {
  typical: number;
  max: number;
}

export interface Plan {
  steps: PlanStep[];
  estimate?: Estimate | null;
  edited?: boolean;
  revision?: number;
}

export type DecisionAction = "approve" | "edit" | "cancel" | "simple";
export type DecisionKind = "plan" | "deep_quota_offer";

export interface Decision {
  action: DecisionAction;
  plan?: Plan;
}

/** One `plan_invalid` reason; `step` is null for plan-level problems. */
export interface PlanReason {
  step: string | null;
  field: string;
  reason: string;
}

export interface Run {
  run_id: string;
  status: RunStatus;
  question: string;
  language: Language;
  answer: Report | null;
  citations: Citation[];
  usage: Record<string, unknown>;
  error_code: string | null;
  created_at: string;
  finished_at: string | null;
  engine?: "graph" | "single";
  mode?: "simple" | "deep" | null;
  plan?: Plan | null;
  plan_expires_at?: string | null;
  decision?: DecisionAction | null;
}

export interface QuotaStatus {
  limit: number;
  used: number;
  remaining: number;
  resets_at: string;
}

export interface Quotas {
  simple: QuotaStatus;
  deep: QuotaStatus;
}

export interface ResearchStarted {
  run_id: string;
  quota?: Quotas;
}

export interface DecisionAccepted {
  run_id: string;
  status: "running" | "cancelled";
  quota: Quotas;
}

export class ApiError extends Error {
  constructor(
    readonly code: ErrorCode,
    message: string,
    readonly status: number,
    readonly retryAfter: number | null = null,
    readonly details: Record<string, unknown> | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** The per-step reasons of a `plan_invalid` error, or [] for any other error. */
  get reasons(): PlanReason[] {
    const reasons = this.details?.reasons;
    return Array.isArray(reasons) ? (reasons as PlanReason[]) : [];
  }
}

/** A report with every Phase 4 field filled, for runs stored before Phase 4 too. */
export function asReport(answer: Report): Required<Report> {
  return {
    ...answer,
    tables: answer.tables ?? [],
    chart: answer.chart ?? null,
    confidence: answer.confidence ?? null,
    unverified: answer.unverified ?? [],
  };
}

export function apiBase(): string {
  return process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
}

/** Navigation used by the auth redirects; tests replace `assign`. */
export const navigation = {
  assign(url: string): void {
    window.location.assign(url);
  },
};

let refreshing: Promise<boolean> | null = null;

/** Rotates the session cookies once; concurrent callers share one refresh. */
export function refreshSession(baseUrl: string = apiBase()): Promise<boolean> {
  refreshing ??= fetch(`${baseUrl}/v1/auth/refresh`, {
    method: "POST",
    credentials: "include",
  })
    .then((response) => response.ok)
    .catch(() => false)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

/** Sends signed-out visitors to sign-in and non-listed ones to the beta page. */
export function redirectForAuth(code: ErrorCode): void {
  if (typeof window === "undefined") return;
  if (code === "unauthorized") {
    const here = `${window.location.pathname}${window.location.search}`;
    navigation.assign(`/signin?return_to=${encodeURIComponent(here)}`);
  } else if (code === "beta_closed") {
    navigation.assign("/beta-closed");
  }
}

async function send(
  path: string,
  init: RequestInit,
  language: Language,
  baseUrl: string,
): Promise<Response> {
  try {
    return await fetch(`${baseUrl}${path}`, {
      ...init,
      credentials: "include",
      headers: {
        Accept: "application/json",
        "Accept-Language": language,
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
  } catch {
    throw new ApiError("dependency_unavailable", "network error", 0);
  }
}

async function toError(response: Response): Promise<ApiError> {
  let code: ErrorCode = "internal_error";
  let message = response.statusText;
  let details: Record<string, unknown> | null = null;
  try {
    const body = (await response.json()) as {
      error?: {
        code?: ErrorCode;
        message?: string;
        details?: Record<string, unknown>;
      };
    };
    code = body.error?.code ?? code;
    message = body.error?.message ?? message;
    details = body.error?.details ?? null;
  } catch {
    // Not the error envelope (proxy page, empty body): keep the generic code.
  }
  const retry = Number(response.headers.get("Retry-After"));
  return new ApiError(
    code,
    message,
    response.status,
    Number.isFinite(retry) && retry > 0 ? retry : null,
    details,
  );
}

async function request<T>(
  path: string,
  init: RequestInit,
  language: Language,
  baseUrl: string,
  redirect = true,
): Promise<T> {
  let response = await send(path, init, language, baseUrl);
  if (response.status === 401 && !path.startsWith("/v1/auth/")) {
    // The 15-minute session ran out: refresh silently and try once more.
    if (await refreshSession(baseUrl)) {
      response = await send(path, init, language, baseUrl);
    }
  }
  if (response.ok) {
    return (response.status === 204 ? undefined : await response.json()) as T;
  }
  const error = await toError(response);
  if (redirect && !path.startsWith("/v1/auth/")) redirectForAuth(error.code);
  throw error;
}

export interface Me {
  display_name: string;
  email_masked: string;
  language: Language;
  quota: Quotas;
}

export function getMe(
  language: Language,
  baseUrl: string = apiBase(),
): Promise<Me> {
  // Signed-out visitors may browse the board, so this call never redirects.
  return request<Me>("/v1/me", { method: "GET" }, language, baseUrl, false);
}

/** Save the language choice on the account; signed out, the 401 is the caller's to ignore. */
export function updateLanguage(
  language: Language,
  baseUrl: string = apiBase(),
): Promise<Me> {
  return request<Me>(
    "/v1/me",
    { method: "PUT", body: JSON.stringify({ language }) },
    language,
    baseUrl,
    false,
  );
}

export type AuthProvider = "google" | "github";

export function startSignIn(
  provider: AuthProvider,
  turnstileToken: string,
  returnTo: string,
  language: Language,
  baseUrl: string = apiBase(),
): Promise<{ authorize_url: string }> {
  return request<{ authorize_url: string }>(
    `/v1/auth/${provider}/start`,
    {
      method: "POST",
      body: JSON.stringify({
        turnstile_token: turnstileToken,
        return_to: returnTo,
      }),
    },
    language,
    baseUrl,
  );
}

export function startResearch(
  question: string,
  language: Language,
  baseUrl: string = apiBase(),
): Promise<ResearchStarted> {
  return request<ResearchStarted>(
    "/v1/research",
    { method: "POST", body: JSON.stringify({ question }) },
    language,
    baseUrl,
  );
}

export function getRun(
  runId: string,
  language: Language,
  baseUrl: string = apiBase(),
): Promise<Run> {
  return request<Run>(
    `/v1/research/${encodeURIComponent(runId)}`,
    { method: "GET" },
    language,
    baseUrl,
  );
}

export function streamUrl(runId: string, baseUrl: string = apiBase()): string {
  return `${baseUrl}/v1/research/${encodeURIComponent(runId)}/stream`;
}

/** Decide a run waiting on a plan or a deep quota offer (contracts/http-api.md). */
export function approveRun(
  runId: string,
  decision: Decision,
  language: Language,
  baseUrl: string = apiBase(),
): Promise<DecisionAccepted> {
  return request<DecisionAccepted>(
    `/v1/research/${encodeURIComponent(runId)}/approve`,
    { method: "POST", body: JSON.stringify(decision) },
    language,
    baseUrl,
  );
}

/** User memory (contracts/http-api.md, "Memory" and "Companies"). */
export type Market = "bist" | "us";
export type PeriodRange = "last_4q" | "last_8q" | "last_2fy" | "last_3fy";
export const PERIOD_RANGES: PeriodRange[] = [
  "last_4q",
  "last_8q",
  "last_2fy",
  "last_3fy",
];

export interface Company {
  ticker: string;
  name: string;
  market: Market;
}

export interface WatchlistItem extends Company {
  added_at: string;
}

export interface Preferences {
  answer_language: Language | null;
  default_period_range: PeriodRange | null;
}

export interface Memory {
  watchlist: WatchlistItem[];
  preferences: Preferences;
  limits: { watchlist_max: number };
}

export function getMemory(
  language: Language,
  baseUrl: string = apiBase(),
): Promise<Memory> {
  return request<Memory>("/v1/me/memory", { method: "GET" }, language, baseUrl);
}

export function savePreferences(
  preferences: Preferences,
  language: Language,
  baseUrl: string = apiBase(),
): Promise<Memory> {
  return request<Memory>(
    "/v1/me/memory/preferences",
    { method: "PUT", body: JSON.stringify(preferences) },
    language,
    baseUrl,
  );
}

export function addToWatchlist(
  ticker: string,
  language: Language,
  baseUrl: string = apiBase(),
): Promise<Memory> {
  return request<Memory>(
    "/v1/me/memory/watchlist",
    { method: "POST", body: JSON.stringify({ ticker }) },
    language,
    baseUrl,
  );
}

export function removeFromWatchlist(
  ticker: string,
  language: Language,
  baseUrl: string = apiBase(),
): Promise<Memory> {
  return request<Memory>(
    `/v1/me/memory/watchlist/${encodeURIComponent(ticker)}`,
    { method: "DELETE" },
    language,
    baseUrl,
  );
}

export function deleteMemory(
  language: Language,
  baseUrl: string = apiBase(),
): Promise<void> {
  return request<void>(
    "/v1/me/memory",
    { method: "DELETE" },
    language,
    baseUrl,
  );
}

export function searchCompanies(
  query: string,
  language: Language,
  limit = 8,
  baseUrl: string = apiBase(),
): Promise<{ items: Company[] }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (query.trim()) params.set("q", query.trim().slice(0, 50));
  return request<{ items: Company[] }>(
    `/v1/companies?${params}`,
    { method: "GET" },
    language,
    baseUrl,
  );
}
