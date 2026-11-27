// The Pub/Sub token kept for the session. No Firebase imports, so the
// application bundle can use it without carrying Firebase.
// Publishing needs a Google access token with the Pub/Sub scope, which
// Firebase hands over once, at sign-in, and does not refresh. It lasts an
// hour; past that, sending asks for it again (usually one click).
export const PUBSUB_SCOPE = 'https://www.googleapis.com/auth/pubsub'
export const TOKEN_KEY = 'dex-remote-pubsub-token'

export function rememberToken(token: string | undefined) {
  if (!token) return
  try {
    sessionStorage.setItem(TOKEN_KEY, JSON.stringify({ token, until: Date.now() + 55 * 60_000 }))
  } catch { /* private mode: asked again next time */ }
}

export function storedToken(): string | null {
  try {
    const saved = JSON.parse(sessionStorage.getItem(TOKEN_KEY) ?? 'null') as { token: string; until: number } | null
    return saved && saved.until > Date.now() ? saved.token : null
  } catch {
    return null
  }
}

export function forgetToken() {
  try { sessionStorage.removeItem(TOKEN_KEY) } catch { /* nothing kept */ }
}
