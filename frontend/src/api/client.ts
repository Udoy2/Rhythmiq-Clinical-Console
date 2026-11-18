import { useAuth } from '../state/auth';
import { API_PREFIX, transport } from './transport';
import type { RefreshResponse } from './types';

export class ApiError extends Error {
  constructor(public status: number, public detail: string, public body?: unknown) {
    super(detail);
    this.name = 'ApiError';
  }
}

/** FastAPI errors are `{detail: string}` or `{detail: [{msg, loc}]}` (validation). */
export function detailFromBody(body: unknown, fallback: string): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) {
      return d.map((e) => (e && typeof e === 'object' && 'msg' in e ? String((e as { msg: unknown }).msg) : String(e))).join('; ');
    }
    if (d && typeof d === 'object') return JSON.stringify(d);
  }
  return fallback;
}

type Query = Record<string, string | number | boolean | null | undefined>;

export function buildUrl(path: string, query?: Query): string {
  const q = new URLSearchParams();
  if (query) for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null && v !== '') q.set(k, String(v));
  const qs = q.toString();
  return `${API_PREFIX}${path}${qs ? `?${qs}` : ''}`;
}

/** Decode the `exp` claim (seconds) of a JWT without verifying it; null if not a JWT. */
export function jwtExp(token: string | null): number | null {
  if (!token) return null;
  const part = token.split('.')[1];
  if (!part) return null;
  try {
    const json = atob(part.replace(/-/g, '+').replace(/_/g, '/'));
    const exp = (JSON.parse(json) as { exp?: unknown }).exp;
    return typeof exp === 'number' ? exp : null;
  } catch {
    return null;
  }
}

let refreshing: Promise<boolean> | null = null;

/** Single-flight refresh with the rotating refresh token. Resolves false (and signs out) when it fails. */
export function refreshTokens(): Promise<boolean> {
  if (refreshing) return refreshing;
  const { refreshToken } = useAuth.getState();
  if (!refreshToken) {
    useAuth.getState().clear();
    return Promise.resolve(false);
  }
  refreshing = (async () => {
    try {
      const res = await transport.fetch(buildUrl('/auth/refresh'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!res.ok) {
        // Only a definite rejection signs the user out; a 5xx keeps the session for a later retry.
        if (res.status === 400 || res.status === 401 || res.status === 403 || res.status === 422) useAuth.getState().clear();
        return false;
      }
      const body = (await res.json()) as RefreshResponse;
      useAuth.getState().setSession({
        accessToken: body.access_token,
        refreshToken: body.refresh_token ?? refreshToken,
        user: body.user,
      });
      return true;
    } catch {
      return false; // network error: keep tokens, caller surfaces the failure
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

/** Access token that is valid for at least `marginS` more seconds, refreshing first if needed. */
export async function freshAccessToken(marginS = 30): Promise<string | null> {
  const { accessToken } = useAuth.getState();
  const exp = jwtExp(accessToken);
  if (accessToken && (exp === null || exp - Date.now() / 1000 > marginS)) return accessToken;
  const ok = await refreshTokens();
  return ok ? useAuth.getState().accessToken : null;
}

interface RequestOpts { query?: Query; body?: unknown; auth?: boolean; signal?: AbortSignal; headers?: Record<string, string> }

async function raw(method: string, path: string, opts: RequestOpts = {}, retried = false): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json', ...opts.headers };
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  const useToken = opts.auth !== false;
  if (useToken) {
    const t = useAuth.getState().accessToken;
    if (t) headers.Authorization = `Bearer ${t}`;
  }
  let res: Response;
  try {
    res = await transport.fetch(buildUrl(path, opts.query), {
      method,
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e;
    throw new ApiError(0, 'Cannot reach the server. Check that the backend is running.');
  }
  if (res.status === 401 && useToken && !retried && useAuth.getState().refreshToken) {
    if (await refreshTokens()) return raw(method, path, opts, true);
  }
  if (!res.ok) {
    let body: unknown;
    try { body = await res.clone().json(); } catch { body = undefined; }
    const fallback =
      res.status === 502 || res.status === 504 ? 'The backend is not reachable (proxy error).'
      : res.status === 403 ? 'You do not have permission to do this.'
      : res.status === 404 ? 'Not found.'
      : `Request failed (${res.status}).`;
    throw new ApiError(res.status, detailFromBody(body, fallback), body);
  }
  return res;
}

export async function request<T>(method: string, path: string, opts: RequestOpts = {}): Promise<T> {
  const res = await raw(method, path, opts);
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError(res.status, 'The server returned a response that is not JSON.');
  }
}

/** Authenticated binary download (PDF / CSV) — Bearer header, so no token in the URL. */
export async function download(path: string, query?: Query): Promise<{ blob: Blob; filename: string | null }> {
  const res = await raw('GET', path, { query, headers: { Accept: '*/*' } });
  const cd = res.headers.get('Content-Disposition');
  const m = cd ? /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd) : null;
  return { blob: await res.blob(), filename: m ? decodeURIComponent(m[1]) : null };
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export const errorText = (e: unknown): string =>
  e instanceof ApiError ? e.detail : e instanceof Error ? e.message : 'Something went wrong.';
