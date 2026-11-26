import { useState, type FormEvent } from 'react'
import { apiGet, auth, AuthError } from '../api'

interface Props {
  /** True when a password was stored but the backend rejected it. */
  rejected: boolean
  onSuccess: () => void
}

/** Shown when the backend runs with DEMO_PASSWORD and answers 401. */
export function LoginModal({ rejected, onSuccess }: Props) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(rejected ? 'That password was not accepted.' : null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!password) return
    setBusy(true)
    setError(null)
    auth.set(password)
    try {
      await apiGet('filters')
      onSuccess()
    } catch (err) {
      if (err instanceof AuthError) {
        auth.clear()
        setError('Incorrect password. Try again.')
      } else {
        setError(`Could not reach the API: ${(err as Error).message}`)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop">
      <form className="modal" onSubmit={submit} aria-labelledby="login-title">
        <div className="brand-mark large" aria-hidden="true">TV</div>
        <h2 id="login-title">TacVector demo</h2>
        <p className="muted">This scouting server is password protected. The password is kept only for this browser tab.</p>
        <label className="field">
          <span>Demo password</span>
          <input
            type="password"
            autoFocus
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-invalid={!!error}
          />
        </label>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="btn primary" type="submit" disabled={busy || !password}>
          {busy ? 'Checking…' : 'Unlock'}
        </button>
      </form>
    </div>
  )
}
