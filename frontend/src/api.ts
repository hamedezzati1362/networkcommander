export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public hint?: string) {
    super(message);
  }
}

function cookie(name: string): string {
  const m = document.cookie.split("; ").find((c) => c.startsWith(name + "="));
  return m ? decodeURIComponent(m.split("=")[1]) : "";
}

export async function api<T = any>(method: string, url: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (method !== "GET") headers["X-NC-CSRF"] = cookie("nc_csrf");
  let res: Response;
  try {
    res = await fetch("/api/v1" + url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "network", "The Network Commander service is not reachable. Is it still running?");
  }
  const text = await res.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { /* not json */ }
  if (!res.ok) {
    if (res.status === 401) throw new ApiError(401, "unauthenticated", "Session missing. Open Network Commander from its launcher (Start menu shortcut).");
    // FastAPI validation errors come as {detail: [...]}
    const msg = data?.message ?? (Array.isArray(data?.detail) ? data.detail.map((d: any) => d.msg).join("; ") : `HTTP ${res.status}`);
    throw new ApiError(res.status, data?.code ?? "error", msg, data?.hint);
  }
  return data as T;
}

export const get = <T = any>(u: string) => api<T>("GET", u);
export const post = <T = any>(u: string, b?: unknown) => api<T>("POST", u, b ?? {});
export const put = <T = any>(u: string, b?: unknown) => api<T>("PUT", u, b ?? {});
export const del = <T = any>(u: string) => api<T>("DELETE", u);
