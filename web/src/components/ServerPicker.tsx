import { useEffect, useState } from 'react'

type Info = {
  default: string
  current: string
  custom: boolean
  cookie: string
  presets: { label: string; origin: string }[]
}

/** Whether an address answered, as /__dex/api-server/probe reports it. */
type Probe = 'checking' | 'up' | 'down'

const hostOf = (origin: string) => new URL(origin)

/**
 * Which dex server this page talks to.
 *
 * The page stays on its own origin; the web server forwards its `/api` to the
 * address chosen here (web/apiServer.ts), kept in a cookie so the choice
 * reaches the server with every request. Reset forgets the choice and goes
 * back to the implicit address. Each server has its own accounts, so moving
 * to another one usually means signing in there.
 *
 * Renders nothing when the page is served without that web server (a build
 * served straight from the API): there is no proxy to point anywhere.
 */
export function ServerPicker() {
  const [info, setInfo] = useState<Info | null>(null)
  const [probes, setProbes] = useState<Record<string, Probe>>({})
  const [scheme, setScheme] = useState('http:')
  const [host, setHost] = useState('')
  const [port, setPort] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    fetch('/__dex/api-server', { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((got: Info | null) => {
        if (!live || !got) return
        setInfo(got)
        const url = hostOf(got.current)
        setScheme(url.protocol)
        setHost(url.hostname)
        setPort(url.port || (url.protocol === 'https:' ? '443' : '80'))
      })
      .catch(() => {})
    return () => { live = false }
  }, [])

  // Every address on the list at once, so the reader sees which are up
  // before picking one.
  useEffect(() => {
    if (!info) return
    const origins = [...new Set([info.default, info.current, ...info.presets.map((p) => p.origin)])]
    setProbes(Object.fromEntries(origins.map((o) => [o, 'checking' as Probe])))
    for (const origin of origins) {
      fetch(`/__dex/api-server/probe?origin=${encodeURIComponent(origin)}`, { cache: 'no-store' })
        .then((r) => r.json())
        .then((r: { status: number | null }) =>
          setProbes((all) => ({ ...all, [origin]: r.status !== null && r.status < 500 ? 'up' : 'down' })),
        )
        .catch(() => setProbes((all) => ({ ...all, [origin]: 'down' })))
    }
  }, [info])

  if (!info) return null

  // A full reload rather than a refetch: the event stream, the signed-in
  // user and every cached list belong to the server being left.
  const choose = (origin: string | null) => {
    document.cookie =
      origin === null
        ? `${info.cookie}=; path=/; max-age=0; samesite=lax`
        : `${info.cookie}=${encodeURIComponent(origin)}; path=/; max-age=31536000; samesite=lax`
    location.reload()
  }

  const typed = (() => {
    try {
      return new URL(`${scheme}//${host.trim()}:${port.trim()}`).origin
    } catch {
      return null
    }
  })()

  const useTyped = () => {
    if (!typed) return setError('That is not an address.')
    setError(null)
    fetch(`/__dex/api-server/probe?origin=${encodeURIComponent(typed)}`, { cache: 'no-store' })
      .then(async (r) => {
        const body = await r.json()
        if (!r.ok) throw new Error(body.error ?? 'Not allowed')
        return body as { origin: string; status: number | null }
      })
      .then((r) => {
        // Choosing an address that does not answer would leave a page that
        // cannot load, with the picker behind it.
        if (r.status === null || r.status >= 500) setError(`Nothing answered at ${r.origin}.`)
        else choose(r.origin)
      })
      .catch((err: Error) => setError(err.message))
  }

  const short = (origin: string) => origin.replace(/^https?:\/\//, '')
  const dot = (origin: string) => (
    <span
      className={`server-dot ${probes[origin] ?? 'checking'}`}
      title={probes[origin] === 'up' ? 'Answering' : probes[origin] === 'down' ? 'Not answering' : 'Checking…'}
    />
  )

  return (
    <div className="server-picker">
      <div className="server-list">
        {info.presets.map((p) => (
          <button
            key={p.origin}
            type="button"
            className={`server-option ${info.current === p.origin ? 'on' : ''}`}
            disabled={info.current === p.origin || probes[p.origin] === 'down'}
            onClick={() => choose(p.origin)}
          >
            {dot(p.origin)}
            <span className="server-label">{p.label}</span>
            <span className="server-origin">{short(p.origin)}</span>
          </button>
        ))}
      </div>
      <div className="server-edit">
        <select value={scheme} onChange={(e) => setScheme(e.target.value)} aria-label="Scheme">
          <option value="http:">http</option>
          <option value="https:">https</option>
        </select>
        <input
          value={host}
          onChange={(e) => setHost(e.target.value)}
          placeholder="host"
          aria-label="Host"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
        />
        <input
          className="server-port"
          value={port}
          onChange={(e) => setPort(e.target.value.replace(/\D/g, ''))}
          placeholder="port"
          aria-label="Port"
          inputMode="numeric"
        />
        <button
          type="button"
          className="ghost-btn small accent"
          disabled={!typed || typed === info.current}
          onClick={useTyped}
        >
          Use
        </button>
      </div>
      {error && <p className="error small">{error}</p>}
      <div className="server-current muted small">
        {dot(info.current)}
        Now: <code>{short(info.current)}</code>
        {info.custom ? (
          <button type="button" className="ghost-btn small" onClick={() => choose(null)}>
            Reset to {short(info.default)}
          </button>
        ) : (
          <span> (the default)</span>
        )}
      </div>
    </div>
  )
}
