// Thin same-origin client for the cerebro gateway. Call sites pass service-relative paths
// (`/memory/...`, `/docs/...`, `/flows/...`, `/auth/...`); the gateway's canonical prefix `/api`
// is added in one place (`apiUrl`). No business logic here: it adds the bearer token and
// normalizes errors.

export const API_PREFIX = '/api';
export const apiUrl = (path: string): string => `${API_PREFIX}${path}`;

export type Session = {
  token: string;
  name: string;
  access_level: 'user' | 'owner' | 'admin' | string;
  allowed_modules: string[] | null;
  expires_at: string;
};

const STORAGE_KEY = 'cerebro.session';
export const SESSION_STORAGE_KEY = STORAGE_KEY;

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function loadSession(): Session | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Session;
    if (!s.token || new Date(s.expires_at).getTime() <= Date.now()) {
      localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return s;
  } catch {
    return null;
  }
}

export function saveSession(s: Session | null): void {
  try {
    if (s) localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage unavailable: the session just won't survive a reload */
  }
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn;
}

function detailToMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : String(d))).join('; ');
  return fallback;
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown; auth?: boolean } = {}): Promise<T> {
  const { json, auth = true, headers, ...rest } = init;
  const h = new Headers(headers);
  if (json !== undefined) h.set('Content-Type', 'application/json');
  const session = auth ? loadSession() : null;
  if (session) h.set('Authorization', `Bearer ${session.token}`);
  let res: Response;
  try {
    res = await fetch(apiUrl(path), { ...rest, headers: h, body: json !== undefined ? JSON.stringify(json) : rest.body });
  } catch {
    throw new ApiError(0, 'Could not reach the server');
  }
  if (res.status === 401 && auth) onUnauthorized?.();
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = (await res.json())?.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detailToMessage(detail, `HTTP ${res.status}`));
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export async function loginWithPassword(name: string, password: string): Promise<Session> {
  return api<Session>('/auth/login/password', { method: 'POST', json: { name, password }, auth: false });
}

export async function logout(): Promise<void> {
  try {
    await api('/auth/logout', { method: 'POST' });
  } catch {
    /* the local session is dropped regardless */
  }
}
