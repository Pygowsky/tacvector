import { useEffect, useState } from 'react'

const PASSWORD_KEY = 'tacvector.demoPassword'

export type Params = Record<string, string | number | null | undefined>

/** Raised on HTTP 401: the backend has DEMO_PASSWORD set and ours is missing or wrong. */
export class AuthError extends Error {
  constructor() {
    super('Demo password required')
  }
}

export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export const auth = {
  get: () => sessionStorage.getItem(PASSWORD_KEY),
  set: (pw: string) => sessionStorage.setItem(PASSWORD_KEY, pw),
  clear: () => sessionStorage.removeItem(PASSWORD_KEY),
}

// A single listener (the App) shows the login modal whenever any request is rejected.
let onAuthRequired: (() => void) | null = null
export function setAuthHandler(fn: (() => void) | null) {
  onAuthRequired = fn
}

export function apiUrl(path: string, params: Params = {}): string {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== '') qs.set(k, String(v))
  }
  const q = qs.toString()
  return `/api/${path}${q ? `?${q}` : ''}`
}

export async function apiGet<T>(path: string, params: Params = {}, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  const pw = auth.get()
  if (pw) headers['X-Demo-Password'] = pw
  const res = await fetch(apiUrl(path, params), { headers, signal })
  if (res.status === 401) {
    onAuthRequired?.()
    throw new AuthError()
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail)
  }
  return (await res.json()) as T
}

// --------------------------------------------------------------------------- data hook

const cache = new Map<string, unknown>()
const CACHE_LIMIT = 40

interface ApiState<T> {
  data: T | undefined
  error: Error | undefined
  loading: boolean
}

/**
 * Fetch `path` with `params` (null = don't fetch). Responses are cached by URL, so toggling
 * between lenses or back to an earlier filter combination is instant. While a new request is in
 * flight the previous data stays visible (`loading` is true) to avoid layout flashes.
 * Bump `nonce` to refetch after logging in.
 */
export function useApi<T>(path: string, params: Params | null, nonce = 0): ApiState<T> {
  const key = params ? apiUrl(path, params) : null
  const [state, setState] = useState<ApiState<T> & { key: string | null }>({
    key: null, data: undefined, error: undefined, loading: false,
  })

  useEffect(() => {
    if (!key || !params) return
    if (cache.has(key)) {
      setState({ key, data: cache.get(key) as T, error: undefined, loading: false })
      return
    }
    const ac = new AbortController()
    setState((s) => ({ ...s, key, error: undefined, loading: true }))
    apiGet<T>(path, params, ac.signal)
      .then((data) => {
        cache.set(key, data)
        if (cache.size > CACHE_LIMIT) cache.delete(cache.keys().next().value as string)
        setState({ key, data, error: undefined, loading: false })
      })
      .catch((error: Error) => {
        if (ac.signal.aborted) return
        setState((s) => ({ ...s, key, error, loading: false }))
      })
    return () => ac.abort()
    // `params` is captured through `key`, which serialises it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, path, nonce])

  return { data: state.data, error: state.error, loading: state.loading }
}
