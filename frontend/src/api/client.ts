// Thin HTTP client for the OBS API (RULE 3: display layer only — no business
// logic, never constructs SQL, never owns authorization).
//
// Auth model (Security Spec v1.4 §6, BFF pattern): the access token lives ONLY in
// memory here (never localStorage). When auth is enabled, a 401 triggers one
// silent refresh against the backend's /api/v1/auth/refresh (which reads the
// httpOnly refresh cookie) and the request is retried once; if that fails the
// caller is redirected to login. Locally (VITE_AUTH_ENABLED=false) no token is
// sent and the backend mock-auth middleware accepts the request.

const AUTH_ENABLED = import.meta.env.VITE_AUTH_ENABLED === "true";

let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : `Request failed (${status})`);
    this.status = status;
    this.detail = detail;
  }
}

async function refreshAccessToken(): Promise<boolean> {
  try {
    const resp = await fetch("/api/v1/auth/refresh", { method: "POST" });
    if (!resp.ok) return false;
    const body = (await resp.json()) as { access_token?: string };
    accessToken = body.access_token ?? null;
    return Boolean(accessToken);
  } catch {
    return false;
  }
}

function buildHeaders(body: unknown): Headers {
  const headers = new Headers();
  if (body !== undefined) headers.set("Content-Type", "application/json");
  if (AUTH_ENABLED && accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  return headers;
}

async function parse<T>(resp: Response): Promise<T> {
  if (resp.status === 204) return undefined as T;
  const text = await resp.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

async function request<T>(method: string, path: string, body?: unknown, retry = true): Promise<T> {
  const resp = await fetch(path, {
    method,
    headers: buildHeaders(body),
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (resp.status === 401 && AUTH_ENABLED && retry) {
    if (await refreshAccessToken()) return request<T>(method, path, body, false);
    redirectToLogin();
  }

  if (!resp.ok) {
    const errBody = await parse<{ detail?: unknown }>(resp).catch(() => undefined);
    throw new ApiError(resp.status, errBody?.detail ?? resp.statusText);
  }
  return parse<T>(resp);
}

export function redirectToLogin(): void {
  if (AUTH_ENABLED) window.location.href = "/api/v1/auth/login";
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};
