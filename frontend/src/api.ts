// API client. The access token lives only in memory (never localStorage); the
// refresh token and device credential are httpOnly cookies the page cannot read.
export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;
  constructor(status: number, body: any) {
    const msg = typeof body?.message === "string" ? body.message : body?.reason || body?.error || `Request failed (${status})`;
    super(typeof msg === "string" ? msg : JSON.stringify(msg));
    this.status = status;
    this.code = body?.error || body?.reason || "error";
    this.details = body?.details || (typeof body?.message === "object" ? body.message : {});
  }
}

let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;
let onSessionLost: () => void = () => {};

export const setAccessToken = (t: string | null) => { accessToken = t; };
export const setSessionLostHandler = (fn: () => void) => { onSessionLost = fn; };

const baseHeaders = (): Record<string, string> => ({ "X-Requested-With": "kantam" });

export function newIdempotencyKey() {
  return crypto.randomUUID();
}

async function refresh(): Promise<boolean> {
  if (!refreshing) {
    refreshing = (async () => {
      const r = await fetch("/api/auth/refresh", { method: "POST", headers: baseHeaders(), credentials: "same-origin" });
      if (!r.ok) return false;
      const body = await r.json();
      accessToken = body.access_token;
      return true;
    })().finally(() => setTimeout(() => (refreshing = null), 0));
  }
  return refreshing;
}

export async function tryResume(): Promise<any | null> {
  if (!(await refresh())) return null;
  return api("GET", "/api/auth/me");
}

type Opts = { idempotencyKey?: string; form?: FormData; raw?: boolean };

export async function api<T = any>(method: string, url: string, body?: unknown, opts: Opts = {}, retried = false): Promise<T> {
  const headers: Record<string, string> = baseHeaders();
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;
  let payload: BodyInit | undefined;
  if (opts.form) payload = opts.form;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const r = await fetch(url, { method, headers, body: payload, credentials: "same-origin" });
  if (r.status === 401 && !retried && !url.startsWith("/api/auth/")) {
    if (await refresh()) return api(method, url, body, opts, true);
    accessToken = null;
    onSessionLost();
  }
  if (opts.raw) {
    if (!r.ok) throw new ApiError(r.status, await r.json().catch(() => ({})));
    return r as unknown as T;
  }
  const text = await r.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    // An HTML error page instead of JSON (server error or the server is restarting).
    throw new ApiError(r.status, { error: "server_error", message: r.ok ? "The server sent an unexpected reply. Reload the page and try again."
      : `The server could not complete this (error ${r.status}). Try again; if it keeps happening, tell your administrator what you were doing.` });
  }
  if (!r.ok) throw new ApiError(r.status, data);
  return data as T;
}

export const get = <T = any>(url: string, params?: Record<string, string | number | undefined | null>) => {
  const qs = params
    ? "?" + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => [k, String(v)])).toString()
    : "";
  return api<T>("GET", url + (qs === "?" ? "" : qs));
};

// Open a server-rendered document / evidence file (authorised fetch -> blob URL).
export async function openProtected(url: string) {
  const win = window.open("", "_blank");
  const r: Response = await api("GET", url, undefined, { raw: true });
  const blob = await r.blob();
  const objectUrl = URL.createObjectURL(blob);
  if (win) win.location.href = objectUrl;
  setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
}

export async function protectedBlobUrl(url: string) {
  const r: Response = await api("GET", url, undefined, { raw: true });
  return URL.createObjectURL(await r.blob());
}
