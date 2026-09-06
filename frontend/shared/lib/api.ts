/**
 * The one place in the app that calls `fetch`.
 *
 * Every backend route lives under `/api/v1`, so
 * paths passed in here are written *without* that prefix: `api.get('/materials')`
 * hits `/api/v1/materials`. In development Vite proxies `/api` to :8000; in
 * production Uvicorn serves the built SPA from the same origin. Either way the
 * default base is the empty string, i.e. same-origin.
 *
 * A 401 on an authenticated call triggers a single-flight refresh followed by
 * one retry. Concurrent 401s share that one refresh rather than racing each
 * other into a token-rotation storm.
 */

import type { TokenPair } from './types'

const API_PREFIX = '/api/v1'
const ACCESS_KEY = 'notevault.access'
const REFRESH_KEY = 'notevault.refresh'

/** Origin of the API without a trailing slash. Empty means same-origin. */
export const API_BASE: string = (import.meta.env.VITE_API_BASE ?? '').replace(/\/+$/, '')

// ---------------------------------------------------------------------------
// errors
// ---------------------------------------------------------------------------

/**
 * Anything a route can fail with. `detail` is already unwrapped from FastAPI's
 * `{"detail": ...}` body -- including the 422 validation array, which is
 * flattened into a readable sentence rather than dumped as JSON.
 */
export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  readonly body: unknown

  constructor(status: number, detail: string, body: unknown = null) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.body = body
  }

  /** True for the network-is-down case, which deserves different copy. */
  get isNetworkError(): boolean {
    return this.status === 0
  }
}

function extractDetail(status: number, body: unknown, fallback: string): string {
  if (typeof body === 'string' && body.trim()) return body.trim()
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string' && detail.trim()) return detail.trim()
    if (Array.isArray(detail)) {
      // FastAPI 422: [{ loc: ['body', 'price'], msg: 'field required' }, ...]
      const parts = detail
        .map((entry) => {
          if (!entry || typeof entry !== 'object') return null
          const { loc, msg } = entry as { loc?: unknown; msg?: unknown }
          if (typeof msg !== 'string') return null
          const field = Array.isArray(loc) ? loc.filter((p) => p !== 'body').join('.') : ''
          return field ? `${field}: ${msg}` : msg
        })
        .filter((p): p is string => Boolean(p))
      if (parts.length) return parts.join('; ')
    }
  }
  return fallback || `Request failed (${status})`
}

// ---------------------------------------------------------------------------
// token storage
// ---------------------------------------------------------------------------

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    // Private-mode Safari and friends. An unauthenticated app beats a crash.
    return null
  }
}

function writeStorage(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, value)
  } catch {
    /* ignore -- storage is a cache, not the source of truth for a request */
  }
}

export function getToken(): string | null {
  return readStorage(ACCESS_KEY)
}

export function getRefreshToken(): string | null {
  return readStorage(REFRESH_KEY)
}

export function setTokens(tokens: TokenPair): void {
  writeStorage(ACCESS_KEY, tokens.access_token)
  writeStorage(REFRESH_KEY, tokens.refresh_token)
}

export function clearTokens(): void {
  writeStorage(ACCESS_KEY, null)
  writeStorage(REFRESH_KEY, null)
}

// ---------------------------------------------------------------------------
// request plumbing
// ---------------------------------------------------------------------------

export type HttpMethod = 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'

export interface RequestOptions {
  /** JSON body. Mutually exclusive with `form`. */
  json?: unknown
  /** Multipart body. The browser sets the boundary -- never set Content-Type. */
  form?: FormData
  /**
   * Query string source. Typed as `object` on purpose: TypeScript does not give
   * interfaces an implicit index signature, so a `Record<string, unknown>`
   * parameter would reject `MaterialQuery` and every other named filter type.
   * `undefined`, `null` and `''` values are dropped; arrays repeat the key.
   */
  query?: object
  /** Send the bearer token, and refresh-and-retry on 401. Default true. */
  auth?: boolean
  signal?: AbortSignal
  headers?: Record<string, string>
}

type QueryScalar = string | number | boolean

function buildQuery(query: object | undefined): string {
  if (!query) return ''
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query as Record<string, unknown>)) {
    if (value === undefined || value === null || value === '') continue
    const push = (v: unknown): void => {
      if (v === undefined || v === null || v === '') return
      if (typeof v === 'object') return // nothing sensible to serialise
      params.append(key, String(v as QueryScalar))
    }
    if (Array.isArray(value)) value.forEach(push)
    else push(value)
  }
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

function buildUrl(path: string, query: object | undefined): string {
  const normalized = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE}${API_PREFIX}${normalized}${buildQuery(query)}`
}

async function send(
  method: HttpMethod,
  path: string,
  opts: RequestOptions,
  token: string | null,
): Promise<Response> {
  const headers = new Headers(opts.headers)
  headers.set('Accept', 'application/json')

  let body: BodyInit | undefined
  if (opts.form) {
    body = opts.form
  } else if (opts.json !== undefined) {
    headers.set('Content-Type', 'application/json')
    body = JSON.stringify(opts.json)
  }
  if (token) headers.set('Authorization', `Bearer ${token}`)

  try {
    return await fetch(buildUrl(path, opts.query), {
      method,
      headers,
      body,
      signal: opts.signal,
      credentials: 'same-origin',
    })
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    throw new ApiError(0, 'Could not reach the server. Check your connection.', cause)
  }
}

async function parse<T>(res: Response): Promise<T> {
  if (res.status === 204 || res.headers.get('content-length') === '0') {
    if (!res.ok) throw new ApiError(res.status, res.statusText || `Request failed (${res.status})`)
    return undefined as T
  }

  const text = await res.text()
  let body: unknown = null
  if (text) {
    try {
      body = JSON.parse(text) as unknown
    } catch {
      body = text
    }
  }

  if (!res.ok) throw new ApiError(res.status, extractDetail(res.status, body, res.statusText), body)
  return body as T
}

// ---------------------------------------------------------------------------
// single-flight refresh
// ---------------------------------------------------------------------------

let refreshInFlight: Promise<string | null> | null = null

async function performRefresh(): Promise<string | null> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) return null

  const res = await send('POST', '/auth/refresh', { json: { refresh_token: refreshToken } }, null)
  if (!res.ok) {
    clearTokens()
    return null
  }
  try {
    const tokens = await parse<TokenPair>(res)
    setTokens(tokens)
    return tokens.access_token
  } catch {
    clearTokens()
    return null
  }
}

/** All concurrent 401s wait on the same refresh call. */
function refreshAccessToken(): Promise<string | null> {
  if (!refreshInFlight) {
    refreshInFlight = performRefresh().finally(() => {
      refreshInFlight = null
    })
  }
  return refreshInFlight
}

/** Refresh failed: the session is genuinely over. Bounce to the login screen. */
function endSession(): void {
  clearTokens()
  const { pathname, search, hash } = window.location
  if (pathname === '/login' || pathname === '/register') return
  const next = encodeURIComponent(`${pathname}${search}${hash}`)
  window.location.assign(`/login?next=${next}`)
}

// ---------------------------------------------------------------------------
// public surface
// ---------------------------------------------------------------------------

export async function request<T>(
  method: HttpMethod,
  path: string,
  opts: RequestOptions = {},
): Promise<T> {
  const authenticated = opts.auth ?? true
  let res = await send(method, path, opts, authenticated ? getToken() : null)

  // /auth/* owns its own 401s -- a bad password must not look like an expiry.
  if (res.status === 401 && authenticated && !path.startsWith('/auth/')) {
    const token = await refreshAccessToken()
    if (token) {
      res = await send(method, path, opts, token)
    } else {
      endSession()
      throw new ApiError(401, 'Your session has expired. Please sign in again.')
    }
  }

  return parse<T>(res)
}

/** Multipart upload -- `POST /materials` and anything else file-shaped. */
export function uploadForm<T>(
  path: string,
  form: FormData,
  opts: Omit<RequestOptions, 'form' | 'json'> & { method?: Extract<HttpMethod, 'POST' | 'PATCH' | 'PUT'> } = {},
): Promise<T> {
  const { method = 'POST', ...rest } = opts
  return request<T>(method, path, { ...rest, form })
}

/**
 * Binary responses -- the CSV/PDF export (FR 4.5) and the preview stream.
 * Returns the blob and the server-suggested filename from Content-Disposition.
 */
export async function requestBlob(
  path: string,
  opts: RequestOptions = {},
): Promise<{ blob: Blob; filename: string | null }> {
  const authenticated = opts.auth ?? true
  let res = await send('GET', path, opts, authenticated ? getToken() : null)

  if (res.status === 401 && authenticated) {
    const token = await refreshAccessToken()
    if (token) {
      res = await send('GET', path, opts, token)
    } else {
      endSession()
      throw new ApiError(401, 'Your session has expired. Please sign in again.')
    }
  }

  if (!res.ok) {
    const text = await res.text()
    let body: unknown = text
    try {
      body = JSON.parse(text) as unknown
    } catch {
      /* keep the raw text */
    }
    throw new ApiError(res.status, extractDetail(res.status, body, res.statusText), body)
  }

  const disposition = res.headers.get('content-disposition') ?? ''
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition)
  return { blob: await res.blob(), filename: match ? decodeURIComponent(match[1]) : null }
}

export const api = {
  get: <T>(path: string, opts?: RequestOptions): Promise<T> => request<T>('GET', path, opts),

  post: <T>(path: string, json?: unknown, opts?: RequestOptions): Promise<T> =>
    request<T>('POST', path, { ...opts, json }),

  patch: <T>(path: string, json?: unknown, opts?: RequestOptions): Promise<T> =>
    request<T>('PATCH', path, { ...opts, json }),

  put: <T>(path: string, json?: unknown, opts?: RequestOptions): Promise<T> =>
    request<T>('PUT', path, { ...opts, json }),

  del: <T>(path: string, opts?: RequestOptions): Promise<T> => request<T>('DELETE', path, opts),

  upload: uploadForm,
  blob: requestBlob,
}

/**
 * URL for a WebSocket route -- `/ws/chat`, `/ws/notifications`. These sit
 * outside `/api/v1`. Browsers cannot set headers on a WS handshake, so the JWT
 * goes in the query string and the server validates it before `accept()`.
 */
export function wsUrl(path: string, token?: string | null): string {
  const origin = API_BASE || window.location.origin
  const url = new URL(path.startsWith('/') ? path : `/${path}`, origin)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  const bearer = token === undefined ? getToken() : token
  if (bearer) url.searchParams.set('token', bearer)
  return url.toString()
}

/** Absolute URL for a streamed file, e.g. a preview to hand to react-pdf. */
export function fileUrl(path: string, query?: object): string {
  return buildUrl(path, query)
}
