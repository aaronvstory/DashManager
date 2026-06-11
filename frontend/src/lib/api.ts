/**
 * Tiny typed fetch helpers. All backend routes live under the '/api' prefix
 * (proxied to http://127.0.0.1:8765 by Vite in dev — see vite.config.ts).
 */

const BASE = "/api"

export class ApiError extends Error {
  status: number
  body: string

  constructor(status: number, statusText: string, body: string) {
    super(`API ${status} ${statusText}: ${body.slice(0, 300)}`)
    this.name = "ApiError"
    this.status = status
    this.body = body
  }
}

async function request<T>(
  method: "GET" | "POST" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    throw new ApiError(res.status, res.statusText, await res.text().catch(() => ""))
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T = void>(path: string) => request<T>("DELETE", path),
}
