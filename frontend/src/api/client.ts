/**
 * Typed HTTP client for the local API.
 *
 * All requests are same-origin and relative, so changing APP_PORT needs no
 * rebuild (docs/09 section 11). Mutations carry the CSRF token bound to the
 * session cookie issued by GET /api/v1/session (docs/08 section 1).
 */

export interface ApiErrorBody {
  code: string;
  message: string;
  requestId: string;
  retryable: boolean;
  [key: string]: unknown;
}

export class ApiError extends Error {
  readonly status: number;
  readonly body: ApiErrorBody;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message || body.code);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }

  get code(): string {
    return this.body.code;
  }
}

let csrfToken: string | null = null;
let csrfHeaderName = "X-CSRF-Token";
let sessionPromise: Promise<void> | null = null;

async function ensureSession(): Promise<void> {
  if (csrfToken) return;
  if (!sessionPromise) {
    sessionPromise = fetch("/api/v1/session", { credentials: "same-origin" })
      .then(async (response) => {
        if (!response.ok) throw new Error("Cannot establish local session");
        const payload = (await response.json()) as { csrfToken: string; headerName: string };
        csrfToken = payload.csrfToken;
        csrfHeaderName = payload.headerName;
      })
      .finally(() => {
        sessionPromise = null;
      });
  }
  await sessionPromise;
}

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export async function request<T>(
  path: string,
  options: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };

  if (!SAFE_METHODS.has(method)) {
    await ensureSession();
    if (csrfToken) headers[csrfHeaderName] = csrfToken;
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
  }

  const response = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    ...(options.signal ? { signal: options.signal } : {}),
  });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};

  if (!response.ok) {
    const body: ApiErrorBody = payload?.error ?? {
      code: "UNKNOWN",
      message: response.statusText,
      requestId: "unknown",
      retryable: false,
    };
    // A restart invalidates the session; retry once with a fresh token.
    if (response.status === 403 && csrfToken && !SAFE_METHODS.has(method)) {
      csrfToken = null;
      await ensureSession();
      return request<T>(path, options);
    }
    throw new ApiError(response.status, body);
  }

  return payload as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) =>
    request<T>(path, signal ? { signal } : {}),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: "POST", body }),
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: "PUT", body }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: "PATCH", body }),
  delete: <T>(path: string, body?: unknown) => request<T>(path, { method: "DELETE", body }),
};
