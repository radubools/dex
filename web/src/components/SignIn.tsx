import { useEffect, useState } from 'react'
import { logout, signInUrl, type AuthUser } from '../api'

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
 * reachable by anyone who finds the URL.
 */
export function SignIn() {
  const reason = useAuthError()
  // Come back to whatever they were trying to open, not always the root.
  const next = location.pathname + location.search
  return (
    <div className="gate">
      <div className="gate-card">
        <h1>dex</h1>
        <p className="muted">Sign in to continue.</p>
        {reason && <p className="error gate-error">{reason}</p>}
        <a className="gate-btn" href={signInUrl(next)}>
          Continue with Google
        </a>
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
