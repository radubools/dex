import { useEffect, useState } from 'react'
import {
  changePassword,
  getAuthConfig,
  login,
  logout,
  signInUrl,
  type AuthConfig,
  type AuthUser,
} from '../api'

/** Why a sign-in attempt bounced, as the callback reports it in `?auth_error=`. */
const REASONS: Record<string, string> = {
  denied: 'You cancelled the Google sign-in.',
  incomplete: 'Google sent an incomplete response. Try again.',
  expired: 'That sign-in attempt expired or was already used. Try again.',
  exchange: 'dex could not complete the exchange with Google. Check the server logs.',
  no_id_token: 'Google did not return an identity token.',
  domain: 'That account is outside the domain this dex allows.',
  unverified: 'That Google account has no verified email address.',
  no_email: 'Google returned no email address for that account.',
}

/** The reason, read once from the URL and then cleared so a reload is clean. */
function useAuthError(): string | null {
  const [reason, setReason] = useState<string | null>(null)
  useEffect(() => {
    const code = new URLSearchParams(location.search).get('auth_error')
    if (!code) return
    setReason(REASONS[code] ?? 'Sign-in failed.')
    const url = new URL(location.href)
    url.searchParams.delete('auth_error')
    history.replaceState(null, '', url.pathname + url.search + url.hash)
  }, [])
  return reason
}

/**
 * The sign-in page, for a visitor dex has never seen.
 *
 * Deliberately says nothing about what dex is or who else uses it: this page is
 * reachable by anyone who finds the URL. That includes the failure text — one
 * message for a wrong password and for a username that does not exist, because
 * telling them apart is how an account list gets enumerated.
 */
export function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const reason = useAuthError()
  const [config, setConfig] = useState<AuthConfig | null>(null)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Come back to whatever they were trying to open, not always the root.
  const next = location.pathname + location.search

  useEffect(() => {
    // Which methods to offer is the server's to say. Rendering both and
    // letting one 404 would show a Google button on an install with no client.
    getAuthConfig()
      .then(setConfig)
      .catch(() => setConfig(null))
  }, [])

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(username, password)
      // The cookie is set; the parent re-reads /me and routes from there —
      // including to the password form, which is a state, not a page.
      onSignedIn()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  const both = config?.googleEnabled && config?.passwordEnabled

  return (
    <div className="gate">
      <div className="gate-card">
        <h1>dex</h1>
        <p className="muted">Sign in to continue.</p>
        {reason && <p className="error gate-error">{reason}</p>}
        {error && <p className="error gate-error">{error}</p>}

        {config?.passwordEnabled && (
          <form className="gate-form" onSubmit={submit}>
            <label>
              <span>Username</span>
              <input
                value={username}
                autoComplete="username"
                autoFocus
                disabled={busy}
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
            <label>
              <span>Password</span>
              <input
                type="password"
                value={password}
                autoComplete="current-password"
                disabled={busy}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            <button className="gate-btn" disabled={busy || !username || !password}>
              {busy ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        )}

        {both && <div className="gate-or">or</div>}

        {config?.googleEnabled && (
          <a className="gate-btn secondary" href={signInUrl(next)}>
            Continue with Google
          </a>
        )}

        {config && !config.googleEnabled && !config.passwordEnabled && (
          <p className="muted small">
            No sign-in method is configured on this server. Set either a Google client or
            <code> DEX_PASSWORD_AUTH=1</code> and restart.
          </p>
        )}
      </div>
    </div>
  )
}

/**
 * Signed in, with a role, and holding a password somebody else chose.
 *
 * Its own gate rather than a banner inside the app: until this is done every
 * other request answers 428, so an app rendered behind it would be a screen of
 * failures. Two people know the current password, which is the whole reason
 * this screen exists.
 */
export function ChangePassword({
  user,
  onChanged,
  onSignedOut,
  onCancel,
  voluntary = false,
}: {
  user: AuthUser | null
  onChanged: () => void
  onSignedOut: () => void
  onCancel?: () => void
  /** True when they chose to come here, rather than being sent. */
  voluntary?: boolean
}) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [minLength, setMinLength] = useState(8)

  useEffect(() => {
    getAuthConfig()
      .then((c) => setMinLength(c.minPasswordLength))
      .catch(() => undefined)
  }, [])

  const mismatch = again.length > 0 && next !== again
  const tooShort = next.length > 0 && next.length < minLength

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await changePassword(current, next)
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="gate">
      <div className="gate-card">
        <h1>{voluntary ? 'Change your password' : 'Choose a password'}</h1>
        <p className="muted">
          {voluntary ? (
            <>
              For <strong>{user?.username ?? user?.email ?? 'this account'}</strong>. Every other
              session you have open will be signed out.
            </>
          ) : (
            <>
              The password on <strong>{user?.username ?? user?.email ?? 'this account'}</strong>{' '}
              was set by an administrator, so two people know it. Replace it to continue.
            </>
          )}
        </p>
        {error && <p className="error gate-error">{error}</p>}
        <form className="gate-form" onSubmit={submit}>
          <label>
            <span>Current password</span>
            <input
              type="password"
              value={current}
              autoComplete="current-password"
              autoFocus
              disabled={busy}
              onChange={(e) => setCurrent(e.target.value)}
            />
          </label>
          <label>
            <span>New password</span>
            <input
              type="password"
              value={next}
              autoComplete="new-password"
              disabled={busy}
              onChange={(e) => setNext(e.target.value)}
            />
          </label>
          <label>
            <span>New password again</span>
            <input
              type="password"
              value={again}
              autoComplete="new-password"
              disabled={busy}
              onChange={(e) => setAgain(e.target.value)}
            />
          </label>
          {tooShort && <p className="muted small">At least {minLength} characters.</p>}
          {mismatch && <p className="muted small">Those two do not match.</p>}
          <button
            className="gate-btn"
            disabled={busy || !current || !next || mismatch || tooShort}
          >
            {busy ? 'Saving…' : 'Set password'}
          </button>
        </form>
        <div className="gate-actions">
          {/* Cancelling is only offered when they chose to be here. On the
              forced path there is nothing to go back to — every other request
              answers 428 — so the only other way out is signing out. */}
          {voluntary && onCancel ? (
            <button className="ghost-btn" disabled={busy} onClick={onCancel}>
              Cancel
            </button>
          ) : (
            <button
              className="ghost-btn"
              disabled={busy}
              onClick={() => void logout().finally(onSignedOut)}
            >
              Sign out
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

/**
 * Signed in, but with no role — so there is nothing to show yet.
 *
 * A distinct page rather than a redirect back to sign-in, which is what a bare
 * 401 would have caused: they are authenticated, so bouncing them to Google
 * would succeed and land them straight back here, forever.
 */
export function Unauthorized({ user, onSignedOut }: { user: AuthUser | null; onSignedOut: () => void }) {
  const [busy, setBusy] = useState(false)
  return (
    <div className="gate">
      <div className="gate-card">
        <h1>Waiting for access</h1>
        <p className="muted">
          You are signed in as <strong>{user?.email ?? 'an unknown account'}</strong>, but no
          role has been assigned to this account yet.
        </p>
        <p className="muted small">
          An administrator has to give you a role and choose which projects you can see. Your
          account is already listed for them — there is nothing more for you to do here.
        </p>
        <div className="gate-actions">
          <button className="ghost-btn" onClick={() => location.reload()}>
            Check again
          </button>
          <button
            className="ghost-btn"
            disabled={busy}
            onClick={() => {
              setBusy(true)
              void logout().finally(onSignedOut)
            }}
          >
            {busy ? '…' : 'Sign out'}
          </button>
        </div>
      </div>
    </div>
  )
}
