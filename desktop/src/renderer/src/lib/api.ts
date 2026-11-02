// Backend connection: Electron passes {url, token} through the preload bridge; in browser mode
// (development) they come from ?api=…&token=… and are remembered for the session.

export interface BackendConfig {
  url: string
  token: string
}

function resolveBackend(): BackendConfig {
  if (window.a4s?.backend) return window.a4s.backend
  const params = new URLSearchParams(window.location.search)
  const url = params.get('api') || sessionStorage.getItem('a4s.api') || 'http://127.0.0.1:8765'
  const token = params.get('token') || sessionStorage.getItem('a4s.token') || ''
  try {
    sessionStorage.setItem('a4s.api', url)
    sessionStorage.setItem('a4s.token', token)
  } catch {
    // storage unavailable
  }
  return { url, token }
}

export let backend: BackendConfig = resolveBackend()

export function setBackend(config: BackendConfig): void {
  backend = config
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message)
  }
}

type Query = Record<string, string | number | boolean | null | undefined>

function buildUrl(path: string, query?: Query): string {
  const url = new URL(`${backend.url}/api${path}`)
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v))
  }
  return url.toString()
}

export async function api<T = unknown>(
  path: string,
  options: { method?: string; body?: unknown; query?: Query; text?: boolean } = {},
): Promise<T> {
  const res = await fetch(buildUrl(path, options.query), {
    method: options.method ?? (options.body !== undefined ? 'POST' : 'GET'),
    headers: {
      Authorization: `Bearer ${backend.token}`,
      ...(options.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const data = await res.json()
      detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    } catch {
      // not JSON
    }
    throw new ApiError(detail || `HTTP ${res.status}`, res.status)
  }
  if (options.text) return (await res.text()) as T
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export const get = <T>(path: string, query?: Query) => api<T>(path, { query })
export const post = <T>(path: string, body: unknown = {}, query?: Query) => api<T>(path, { method: 'POST', body, query })
export const put = <T>(path: string, body: unknown) => api<T>(path, { method: 'PUT', body })
export const patch = <T>(path: string, body: unknown) => api<T>(path, { method: 'PATCH', body })
export const del = <T>(path: string, query?: Query) => api<T>(path, { method: 'DELETE', query })

/** Direct URL for an imported asset — `<img>` / `<video>` cannot send an Authorization header,
 *  so the launch token rides in the query string (the backend accepts it there). */
export function assetUrl(assetId: string): string {
  return `${backend.url}/api/assets/${encodeURIComponent(assetId)}/raw?token=${encodeURIComponent(backend.token)}`
}

export function wsUrl(): string {
  const url = new URL(backend.url)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.pathname = '/ws'
  url.searchParams.set('token', backend.token)
  return url.toString()
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return String(err)
}
