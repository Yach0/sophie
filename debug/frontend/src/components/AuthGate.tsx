import { type FormEvent, type ReactNode, useEffect, useState } from 'react'
import { t } from 'ttag'
import { ApiError, api, authenticate } from '../api/client'
import type { Session } from '../api/types'

interface AuthGateProps {
  children: (initialSession: Session) => ReactNode
}

export function AuthGate({ children }: AuthGateProps) {
  const [session, setSession] = useState<Session | null>(null)
  const [token, setToken] = useState('')
  const [checking, setChecking] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    const bootstrap = async () => {
      setChecking(true)
      setError(null)
      try {
        setSession(await api<Session>('/session', { signal: controller.signal }))
      } catch (reason) {
        if (controller.signal.aborted) return
        if (!(reason instanceof ApiError) || reason.status !== 401) {
          setError(reason instanceof Error ? reason.message : t`Unable to contact the debugger.`)
        }
      } finally {
        if (!controller.signal.aborted) setChecking(false)
      }
    }
    void bootstrap()
    return () => controller.abort()
  }, [])

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!token.trim()) return
    setChecking(true)
    setError(null)
    try {
      await authenticate(token.trim())
      setSession(await api<Session>('/session'))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t`Authentication failed.`)
    } finally {
      setToken('')
      setChecking(false)
    }
  }

  if (session) return children(session)

  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="auth-title">
        <p className="eyebrow">{t`Local development tool`}</p>
        <h1 id="auth-title">{t`Open Sophie Debugger`}</h1>
        <p>
          {t`Paste the session token from data/debug-session.json. It is exchanged once for a private browser-session cookie and is not stored by this page.`}
        </p>
        <form onSubmit={submit}>
          <label htmlFor="session-token">{t`Session token`}</label>
          <input
            id="session-token"
            type="password"
            value={token}
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => setToken(event.currentTarget.value)}
          />
          <button type="submit" disabled={checking || !token.trim()}>
            {checking ? t`Checking…` : t`Authenticate`}
          </button>
        </form>
        {error && <div className="notice error" role="alert">{error}</div>}
        {!error && checking && <p role="status">{t`Checking for an existing local session…`}</p>}
      </section>
    </main>
  )
}
