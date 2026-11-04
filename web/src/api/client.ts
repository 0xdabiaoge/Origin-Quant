/** Single fetch wrapper: every call goes through here so auth failures and
 *  network errors are handled in one place instead of in every view. */

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

async function request<T>(path: string, init: RequestInit = {}, redirectOnUnauthorized = true): Promise<T> {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
    ...init,
  });

  if (res.status === 401 && redirectOnUnauthorized) {
    onUnauthorized?.();
    throw new ApiError(401, "登录已失效，请重新登录");
  }

  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }

  if (!res.ok) {
    const raw = body && typeof body === "object" && "detail" in body
      ? (body as { detail: unknown }).detail : null;
    const detail = Array.isArray(raw)
      ? raw.map((item: unknown) => item && typeof item === "object" && "msg" in item
        ? String((item as { msg: unknown }).msg) : "输入不符合要求").join("；")
      : typeof raw === "string" ? raw : `HTTP ${res.status}`;
    throw new ApiError(res.status, detail);
  }
  return body as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body === undefined ? undefined : JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

export const v1 = {
  marketSummary: () => api.get<{ items: import("./types").MarketQuote[]; now_ms: number; base_timeframe: string }>("/api/v1/app/market/summary"),
  candles: (symbol: string, timeframe: string) => api.get<import("./types").CandlesPayload>(`/api/v1/app/market/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`),
  brand: () => api.get<import("./types").Brand>("/api/v1/public/brand"),
  metrics: () => api.get<import("./types").PublicMetrics>("/api/v1/public/metrics"),
  overview: () => api.get<import("./types").Overview>("/api/v1/app/overview"),
  positions: () => api.get<import("./types").PositionsPayload>("/api/v1/app/positions"),
  orders: () => api.get<import("./types").OrdersPayload>("/api/v1/app/orders"),
  decisions: (limit = 100) =>
    api.get<import("./types").DecisionsPayload>(`/api/v1/app/decisions?limit=${limit}`),
  events: (limit = 100) => api.get<{ items: import("./types").EventRow[] }>(`/api/v1/app/events?limit=${limit}`),
  equity: (limit = 600) =>
    api.get<{ items: import("./types").EquityPoint[] }>(`/api/v1/app/equity?limit=${limit}`),
  data: () => api.get<import("./types").DataPayload>("/api/v1/app/data"),
  me: () => request<import("./types").Me>("/api/v1/auth/me", {}, false),
  login: (username: string, password: string) =>
    request<{ ok: boolean; username: string; role: string }>(
      "/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }, false,
    ),
  logout: () => api.post<{ ok: boolean }>("/api/auth/logout"),
  adminOverview: () => api.get<import("./types").AdminOverview>("/api/v1/admin/overview"),
  aiConfig: () => api.get<import("./types").AiConfig>("/api/v1/admin/ai/config"),
  saveAiConfig: (body: Omit<import("./types").AiConfig, "has_key">) =>
    api.put<import("./types").AiConfig>("/api/v1/admin/ai/config", body),
  aiHistory: () => api.get<{ items: import("./types").AiDecision[] }>("/api/v1/admin/ai/history"),
  testAi: () => api.post<{ ok: boolean; provider: string; model: string }>("/api/v1/admin/ai/test"),
  aiAnalyze: (symbol: string) => api.post<import("./types").AiDecision>("/api/v1/admin/ai/analyze", { symbol }),
  news: () => api.get<{ items: import("./types").NewsItem[]; fetched_ms: number | null; errors: Record<string, string> }>("/api/v1/admin/news"),
  refreshNews: () => api.post<unknown>("/api/v1/admin/news/refresh"),
  venueReadback: () => api.post<import("./types").VenueReadback>("/api/v1/admin/venue/readback"),
  users: () => api.get<{ items: import("./types").UserRow[]; roles: string[] }>("/api/v1/admin/users"),
  createUser: (body: { username: string; password: string; role: string }) =>
    api.post<{ ok: boolean }>("/api/v1/admin/users", body),
  setPassword: (username: string, password: string) =>
    api.put<{ ok: boolean }>(`/api/v1/admin/users/${username}/password`, { password }),
  setRole: (username: string, role: string) =>
    api.put<{ ok: boolean }>(`/api/v1/admin/users/${username}/role`, { role }),
  deleteUser: (username: string) =>
    api.del<{ ok: boolean }>(`/api/v1/admin/users/${username}`),
  audit: (limit = 100, offset = 0) =>
    api.get<{ items: import("./types").AuditRow[]; total: number }>(
      `/api/v1/admin/audit?limit=${limit}&offset=${offset}`,
    ),
  config: () => api.get<{ config: unknown; hash: string; paths: Record<string, string> }>("/api/v1/admin/config"),
  logs: (limit = 200) =>
    api.get<{ path: string | null; available: boolean; lines: string[] }>(`/api/v1/admin/logs?limit=${limit}`),
  backup: () => api.post<{ ok: boolean; file: string; bytes: number }>("/api/v1/admin/backup"),
  backups: () =>
    api.get<{ items: { file: string; bytes: number; created_ms: number }[]; disk_free?: number }>(
      "/api/v1/admin/backups",
    ),
  danger: (action: string, confirm: string) =>
    api.post<{ ok: boolean; action: string; venue_flat_confirmed?: boolean; outcomes?: { symbol: string; book: string; status: string; errors: string[]; remaining_local_qty?: string }[] }>(`/api/v1/admin/danger/${action}`, { confirm }),
  cycle: () => api.post<Record<string, unknown>>("/api/control/cycle"),
  backtest: (body: { days: number; source: string; cash?: string }) =>
    api.post<import("./types").BacktestResult>("/api/backtest", body),
};
