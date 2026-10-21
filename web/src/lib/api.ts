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

async function request<T>(
  path: string,
  init: RequestInit,
  language: Language,
  baseUrl: string,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, {
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
  if (response.ok) return (await response.json()) as T;
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
  throw new ApiError(
    code,
    message,
    response.status,
    Number.isFinite(retry) && retry > 0 ? retry : null,
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
