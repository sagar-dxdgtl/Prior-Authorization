const API_AUTH = (import.meta.env.VITE_API_AUTH as string | undefined) ?? 'http://localhost:8000/api/auth';
const API = (import.meta.env.VITE_API as string | undefined) ?? 'http://localhost:8000/api';

const TOKEN_KEY = 'auth_token';
const REFRESH_KEY = 'refresh_token';
const EXPIRES_KEY = 'auth_expires_at';
const USER_KEY = 'user_info';

export interface User {
  id: number;
  username: string;
  role: string;
  tenant_id: number;
}

export interface LoginResult {
  must_change_password?: true;
  access_token?: string;
  expires_in?: number;
  refresh_token?: string;
  tokens?: { access: string };
  user?: User;
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function isAuthenticated(): boolean {
  const token = getToken();
  if (!token) return false;
  const exp = localStorage.getItem(EXPIRES_KEY);
  if (exp) return Date.now() < Number(exp);
  return true;
}

export function getUser(): User | null {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as User;
  } catch {
    return null;
  }
}

function storeTokens(access: string, expiresIn?: number, refreshToken?: string, user?: User): void {
  localStorage.setItem(TOKEN_KEY, access);
  if (expiresIn != null) {
    localStorage.setItem(EXPIRES_KEY, String(Date.now() + expiresIn * 1000));
  }
  if (refreshToken) localStorage.setItem(REFRESH_KEY, refreshToken);
  if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function logout(): void {
  [TOKEN_KEY, REFRESH_KEY, EXPIRES_KEY, USER_KEY].forEach((k) => localStorage.removeItem(k));
}

export async function login(username: string, password: string): Promise<LoginResult> {
  const body = new URLSearchParams({ grant_type: 'password', username, password });
  const res = await fetch(`${API_AUTH}/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  });
  const data = (await res.json()) as LoginResult & { message?: string };
  if (!res.ok) {
    throw new Error(data.message ?? `Login failed (${res.status})`);
  }
  if (data.must_change_password) {
    // Store temp token for the change-password call
    if (data.tokens?.access) {
      localStorage.setItem(TOKEN_KEY, data.tokens.access);
    }
    return data;
  }
  if (data.access_token) {
    storeTokens(data.access_token, data.expires_in, data.refresh_token, data.user);
  }
  return data;
}

async function doRefresh(): Promise<string> {
  const refreshToken = localStorage.getItem(REFRESH_KEY);
  if (!refreshToken) throw new Error('No refresh token');
  const res = await fetch(`${API_AUTH}/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!res.ok) throw new Error('Refresh failed');
  const data = (await res.json()) as { access_token: string; expires_in?: number };
  localStorage.setItem(TOKEN_KEY, data.access_token);
  if (data.expires_in != null) {
    localStorage.setItem(EXPIRES_KEY, String(Date.now() + data.expires_in * 1000));
  }
  return data.access_token;
}

export async function changePassword(
  current_password: string,
  new_password: string,
  confirm_password: string,
): Promise<void> {
  const token = getToken();
  const res = await fetch(`${API_AUTH}/change-password/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ current_password, new_password, confirm_password }),
  });
  const data = (await res.json()) as { success: boolean; message?: string };
  if (!res.ok || !data.success) {
    throw new Error(data.message ?? 'Password change failed');
  }
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const buildHeaders = (token: string | null): Headers => {
    const h = new Headers(init.headers as HeadersInit | undefined);
    if (token) h.set('Authorization', `Bearer ${token}`);
    if (!h.has('Content-Type') && init.body != null) {
      h.set('Content-Type', 'application/json');
    }
    return h;
  };

  const res = await fetch(`${API}${path}`, { ...init, headers: buildHeaders(getToken()) });

  if (res.status === 401) {
    try {
      const newToken = await doRefresh();
      return fetch(`${API}${path}`, { ...init, headers: buildHeaders(newToken) });
    } catch {
      logout();
      window.location.replace('/login');
      throw new Error('Session expired');
    }
  }

  return res;
}
