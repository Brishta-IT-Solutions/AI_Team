"use client";

// Same origin by default: the web app forwards /v1 to the Control API (see next.config.ts).
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
const SUBJECT_KEY = "aitc.subject";

export type ApiErrorBody = {
  code: string;
  message: string;
  retryable: boolean;
  correlation_id: string | null;
  field_errors: Record<string, string>;
  details?: { unmet?: string[]; missing?: { key: string; detail: string }[]; [k: string]: unknown };
};

export class ApiError extends Error {
  constructor(public status: number, public body: ApiErrorBody) {
    super(body.message);
  }
  get reasons(): string[] {
    return this.body.details?.unmet ?? this.body.details?.missing?.map((m) => m.detail) ?? [];
  }
}

export function getSubject(): string {
  if (typeof window === "undefined") return "product";
  try {
    return window.localStorage.getItem(SUBJECT_KEY) ?? "product";
  } catch {
    return "product";
  }
}

export function setSubject(subject: string): void {
  try {
    window.localStorage.setItem(SUBJECT_KEY, subject);
  } catch {
    /* storage unavailable: identity falls back to default */
  }
  window.dispatchEvent(new Event("aitc:identity"));
}

/** A fresh idempotency key. randomUUID() only exists on https or localhost; the LAN address is plain http. */
function newKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

async function request<T>(method: string, path: string, body?: unknown, idempotencyKey?: string): Promise<T> {
  const headers: Record<string, string> = {
    authorization: `Bearer dev-human:${getSubject()}`,
    "content-type": "application/json",
  };
  if (method !== "GET") headers["idempotency-key"] = idempotencyKey ?? newKey();
  let res: Response;
  try {
    res = await fetch(`${API_URL}/v1${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
  } catch {
    throw new ApiError(0, {
      code: "network",
      message: "Control API is unreachable. Check that it is running.",
      retryable: true,
      correlation_id: null,
      field_errors: {},
    });
  }
  const json = await res.json().catch(() => ({}));
  if (res.status === 401 && (json as ApiErrorBody).code === "access_code_required") {
    window.location.assign(`/access?next=${encodeURIComponent(window.location.pathname)}`);
  }
  if (!res.ok) throw new ApiError(res.status, json as ApiErrorBody);
  return json as T;
}

export const api = {
  get: <T,>(path: string) => request<T>("GET", path),
  post: <T,>(path: string, body: unknown, key?: string) => request<T>("POST", path, body, key),
  put: <T,>(path: string, body: unknown, key?: string) => request<T>("PUT", path, body, key),
};
