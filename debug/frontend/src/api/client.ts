import type { ApiErrorBody, JsonValue } from './types'

let csrfToken: string | null = null

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId: string | null
  readonly statusUrl: string | null

  constructor(status: number, code: string, message: string, requestId: string | null = null, statusUrl: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.requestId = requestId
    this.statusUrl = statusUrl
  }
}

export function setCsrfToken(value: string | null): void {
  csrfToken = value
}

export function takeFragmentToken(): string | null {
  const fragment = window.location.hash.startsWith('#') ? window.location.hash.slice(1) : ''
  const token = new URLSearchParams(fragment).get('token')
  const search = new URLSearchParams(window.location.search)
  const hadQueryToken = search.has('token')
  search.delete('token')
  if (fragment || hadQueryToken) {
    const safeSearch = search.toString()
    window.history.replaceState(null, '', `${window.location.pathname}${safeSearch ? `?${safeSearch}` : ''}`)
  }
  return token?.trim() || null
}

async function parseError(response: Response): Promise<ApiError> {
  let body: ApiErrorBody | null = null
  try {
    body = (await response.json()) as ApiErrorBody
  } catch {
    return new ApiError(response.status, 'http_error', `Request failed with HTTP ${response.status}`)
  }
  return new ApiError(
    response.status,
    body.error?.code ?? 'http_error',
    body.error?.message ?? `Request failed with HTTP ${response.status}`,
    body.error?.request_id ?? null,
    body.error?.status_url ?? null,
  )
}

export async function authenticate(token: string, signal?: AbortSignal): Promise<void> {
  const response = await fetch('/api/v1/session/auth', {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    credentials: 'same-origin',
    signal,
  })
  if (!response.ok) throw await parseError(response)
}

type ApiOptions = Omit<RequestInit, 'body'> & { body?: BodyInit | JsonValue }


export async function api<T>(
  path: string,
  options: ApiOptions = {},
): Promise<T> {
  const headers = new Headers(options.headers)
  const method = (options.method ?? 'GET').toUpperCase()
  let body = options.body
  if (body !== undefined && typeof body !== 'string' && !(body instanceof Blob) && !(body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
    body = JSON.stringify(body)
  }
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && csrfToken) {
    headers.set('X-Debug-CSRF', csrfToken)
  }
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    method,
    body,
    headers,
    credentials: 'same-origin',
  })
  if (!response.ok) throw await parseError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function stringifyRedacted(value: JsonValue | object): string {
  return JSON.stringify(value, null, 2)
}
