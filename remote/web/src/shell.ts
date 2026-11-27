// The public part of the page: everything in the web bucket. It signs people
// in, reads their users/<email> entry, and either says they are not authorised
// or fetches the application from the private files bucket (app/app.js, which
// the Storage rules give only to authorised users) and mounts it. Nothing
// about projects or tasks is in here.
import type { User } from 'firebase/auth'
import { connect, type AppContext, type Fb, type Profile, type RemoteConfig } from './firebase'
import { PUBSUB_SCOPE, rememberToken, storedToken } from './tokens'
import './shell.css'

const root = document.getElementById('root')!

function screen(html: string) {
  root.innerHTML = `<div class="center"><div class="card gate">${html}</div></div>`
}

const escape = (text: string) =>
  text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)

async function signIn(fb: Fb, hint?: string): Promise<string> {
  const provider = new fb.a.GoogleAuthProvider()
  provider.addScope(PUBSUB_SCOPE)
  provider.setCustomParameters(hint ? { login_hint: hint } : { prompt: 'select_account' })
  const result = await fb.a.signInWithPopup(fb.auth, provider)
  const token = fb.a.GoogleAuthProvider.credentialFromResult(result)?.accessToken
  rememberToken(token)
  return token ?? ''
}

function showSignIn(fb: Fb, note = '') {
  screen(`
    <h1>dex</h1>
    <p class="muted">Sign in with your Google account.</p>
    <button class="primary" id="signin">Sign in with Google</button>
    <p class="error small" id="note">${escape(note)}</p>`)
  document.getElementById('signin')!.onclick = () =>
    signIn(fb).catch((e) => {
      document.getElementById('note')!.textContent = String(e?.message ?? e)
    })
}

function showUnauthorised(fb: Fb, user: User) {
  screen(`
    <h1>Not authorised</h1>
    <p class="muted">You are signed in as <b>${escape(user.email ?? '')}</b>, which this dex has
      not authorised. Ask its operator to add you; then reload this page.</p>
    <button class="ghost" id="signout">Sign in with another account</button>`)
  document.getElementById('signout')!.onclick = () => void fb.a.signOut(fb.auth)
}

async function profileOf(fb: Fb, user: User): Promise<Profile | null> {
  const snap = await fb.f.getDoc(fb.f.doc(fb.db, 'users', (user.email ?? '').toLowerCase()))
  const data = snap.data()
  if (!user.emailVerified || !data || !['user', 'admin'].includes(data.role)) return null
  return { role: data.role, projects: data.projects ?? {}, name: data.name }
}

let mounted: { unmount: () => void } | null = null

async function enter(config: RemoteConfig, fb: Fb, user: User) {
  screen('<p class="muted">Checking your access…</p>')
  let profile: Profile | null
  try {
    profile = await profileOf(fb, user)
  } catch {
    profile = null
  }
  if (!profile) return showUnauthorised(fb, user)

  // The application, from the private bucket, as a module.
  const blob = await fb.s.getBlob(fb.s.ref(fb.storage, 'app/app.js'))
  const url = URL.createObjectURL(new Blob([blob], { type: 'text/javascript' }))
  const app = (await import(/* @vite-ignore */ url)) as {
    mount: (el: HTMLElement, ctx: AppContext) => { unmount: () => void }
  }
  URL.revokeObjectURL(url)
  root.innerHTML = ''
  mounted = app.mount(root, {
    config, user, profile, fb,
    pubsubToken: async () => storedToken() ?? (await signIn(fb, user.email ?? undefined)),
  })
}

async function main() {
  let config: RemoteConfig
  try {
    const reply = await fetch('config.json', { cache: 'no-store' })
    if (!reply.ok) throw new Error(`config.json: ${reply.status}`)
    config = await reply.json()
  } catch (e) {
    screen(`<p class="error">This page is not set up: ${escape(String((e as Error).message ?? e))}</p>`)
    return
  }
  const fb = connect(config)
  fb.a.onAuthStateChanged(fb.auth, (user) => {
    mounted?.unmount()
    mounted = null
    if (!user) return showSignIn(fb)
    enter(config, fb, user).catch((e) =>
      screen(`<p class="error">Could not open dex: ${escape(String(e?.message ?? e))}</p>
        <button class="ghost" onclick="location.reload()">Reload</button>`))
  })
}

void main()
