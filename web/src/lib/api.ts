import type { Language } from "@/i18n";

/** Types and calls for the research API (contracts/http-api.md, agent-answer.md). */

export type RunStatus =
  "running" | "completed" | "partial" | "refused" | "no_evidence" | "failed";

export type AnswerStatus = Exclude<RunStatus, "running" | "failed">;

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
  | "providers_unavailable";

export interface Point {
  text: string;
  cite_ids: string[];
}

export interface CitationRef {
  marker: number;
  cite_id: string;
}

export interface Answer {
  language: Language;
  status: AnswerStatus;
  summary: string;
  points: Point[];
  citations: CitationRef[];
  disclaimer: string;
}

export interface CitationSource {
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

export interface Citation {
  cite_id: string;
  excerpt: string;
  source: CitationSource;
}

export interface Run {
  run_id: string;
  status: RunStatus;
  question: string;
  language: Language;
  answer: Answer | null;
  citations: Citation[];
  usage: Record<string, unknown>;
  error_code: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface QuotaStatus {
  limit: number;
  used: number;
  remaining: number;
  resets_at: string;
}

export interface ResearchStarted {
  run_id: string;
  quota?: QuotaStatus;
}

export class ApiError extends Error {
  constructor(
    readonly code: ErrorCode,
    message: string,
    readonly status: number,
    readonly retryAfter: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
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
  try {
    const body = (await response.json()) as {
      error?: { code?: ErrorCode; message?: string };
    };
    code = body.error?.code ?? code;
    message = body.error?.message ?? message;
  } catch {
    // Not the error envelope (proxy page, empty body): keep the generic code.
  }
  const retry = Number(response.headers.get("Retry-After"));
  return new ApiError(
    code,
    message,
    response.status,
    Number.isFinite(retry) && retry > 0 ? retry : null,
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
  quota: QuotaStatus;
}

export function getMe(
  language: Language,
  baseUrl: string = apiBase(),
): Promise<Me> {
  // Signed-out visitors may browse the board, so this call never redirects.
  return request<Me>("/v1/me", { method: "GET" }, language, baseUrl, false);
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
