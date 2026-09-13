import { useEffect, useRef, useState } from 'react'
import { readAsset, type WidgetInfo } from '../api'

/**
 * Runs a project widget in a sandboxed iframe.
 *
 * The whole security argument is in the sandbox attribute: `allow-scripts`
 * without `allow-same-origin`. That gives the frame a unique opaque origin, so
 * it cannot read this page's cookies, cannot call `/api/*` as the signed-in
 * user, and cannot reach into the DOM around it. Widgets are authored by an
 * admin, but "only admins write them" is exactly the assumption that stops
 * being true later, and the sandbox costs almost nothing.
 *
 * Because the origin is opaque, the frame also cannot fetch its own bundle from
 * `/widgets/...` -- that would be a cross-origin request. So the host fetches
 * the bundle as text and inlines it into `srcdoc`. The frame therefore needs no
 * network access at all, which is the strongest version of this: there is
 * nothing for it to reach.
 *
 * Everything it needs arrives by `postMessage`, and every file it asks for is
 * fetched by the host, which is where authorisation lives.
 */

/** Base64 of a byte array, in chunks small enough to spread safely. */
function base64(bytes: Uint8Array): string {
  const CHUNK = 0x8000
  let binary = ''
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK))
  }
  return btoa(binary)
}

/** What the frame may ask the host for, and what the host sends back. */
type FromWidget =
  | { type: 'ready' }
  | { type: 'error'; message: string }
  | { type: 'height'; height: number }
  | { type: 'fetch'; id: string; path: string; binary?: boolean }

const BOOTSTRAP = (code: string, theme: string) => `<!doctype html>
<html data-theme="${theme}">
<head><meta charset="utf-8"><style>
  :root { color-scheme: ${theme}; }
  html,body { margin:0; padding:0; background:transparent;
    font: 14px ui-sans-serif, system-ui, -apple-system, sans-serif;
    color: ${theme === 'dark' ? '#e7e9ee' : '#14161a'}; }
  #root { min-height: 40px; }
  .widget-error { color:#ff6b6b; padding:12px; font-family:ui-monospace,monospace; font-size:12px; white-space:pre-wrap; }
</style></head>
<body><div id="root"></div>
<script type="module">
const send = (m) => parent.postMessage(m, '*')
// Anything the widget throws is reported rather than swallowed: a silent blank
// frame is the worst failure mode for something an agent just wrote.
window.addEventListener('error', (e) => send({ type: 'error', message: String(e.message || e.error) }))
window.addEventListener('unhandledrejection', (e) => send({ type: 'error', message: String(e.reason) }))

const pending = new Map()
let counter = 0
/** Ask the host for another file. The host decides whether we may have it. */
const fetchAsset = (path) => new Promise((resolve, reject) => {
  const id = String(++counter)
  pending.set(id, { resolve, reject })
  send({ type: 'fetch', id, path })
})

/**
 * A URL for a binary sibling -- a video, an image.
 *
 * It cannot be a plain '/api/...' src: this frame has an opaque origin, so any
 * such request is cross-origin and credentialless, and would 401 as soon as
 * sign-in is on. The host fetches the bytes with the session it has and posts
 * them over; the blob URL is then created *here*, where the frame can use it.
 */
const fetchAssetUrl = async (path) => {
  const id = String(++counter)
  const bytes = await new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject })
    send({ type: 'fetch', id, path, binary: true })
  })
  return URL.createObjectURL(new Blob([bytes]))
}

window.addEventListener('message', (event) => {
  const msg = event.data
  if (!msg || typeof msg !== 'object') return
  if (msg.type === 'asset') {
    const waiter = pending.get(msg.id)
    if (!waiter) return
    pending.delete(msg.id)
    msg.error ? waiter.reject(new Error(msg.error)) : waiter.resolve(msg.body)
  }
})

// Report our own height so the host can size the frame to the content.
const report = () => send({ type: 'height', height: document.documentElement.scrollHeight })
new ResizeObserver(report).observe(document.documentElement)

try {
  const mod = await import('data:text/javascript;base64,' + "${code}")
  if (typeof mod.mount !== 'function') throw new Error('widget has no exported mount(el, ctx)')
  const ctx = JSON.parse(document.getElementById('ctx').textContent)
  await mod.mount(document.getElementById('root'), { ...ctx, fetchAsset, fetchAssetUrl })
  send({ type: 'ready' })
  report()
} catch (err) {
  document.getElementById('root').innerHTML =
    '<div class="widget-error">' + String(err && err.stack || err) + '</div>'
  send({ type: 'error', message: String(err) })
}
</script>
</body></html>`

export function WidgetHost({
  widget,
  path,
  text,
  onFallback,
}: {
  widget: WidgetInfo
  path: string
  /** The opened file's content, already fetched by the host. */
  text: string
  /** Called when the widget cannot run, so the viewer can show something. */
  onFallback: (reason: string) => void
}) {
  const frame = useRef<HTMLIFrameElement>(null)
  const [srcDoc, setSrcDoc] = useState<string | null>(null)
  const [height, setHeight] = useState(320)
  const [failed, setFailed] = useState<string | null>(null)

  // Build the document: fetch the bundle as text, inline it, hand over the file.
  useEffect(() => {
    let live = true
    setFailed(null)
    setSrcDoc(null)
    fetch(widget.url)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`widget ${r.status}`))))
      .then((code) => {
        if (!live) return
        const theme =
          document.documentElement.dataset.theme ??
          (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
        // base64 so the bundle's own quotes, backticks and `</script>` cannot
        // terminate the host document early -- inlining raw source into srcdoc
        // is how a widget containing a template literal breaks the page.
        //
        // Chunked, not `String.fromCharCode(...bytes)`: spreading a 646 KB
        // bundle passes 646,000 arguments and throws "Maximum call stack size
        // exceeded". The small widget worked and the three.js one did not,
        // which is the worst way to find out.
        const encoded = base64(new TextEncoder().encode(code))
        const ctx = JSON.stringify({ path, text, theme })
        setSrcDoc(
          BOOTSTRAP(encoded, theme).replace(
            '<div id="root"></div>',
            `<div id="root"></div><script type="application/json" id="ctx">${ctx.replace(
              /</g,
              '\\u003c',
            )}</script>`,
          ),
        )
      })
      .catch((err: Error) => {
        if (!live) return
        setFailed(err.message)
        onFallback(err.message)
      })
    return () => {
      live = false
    }
  }, [widget.url, path, text, onFallback])

  // The bridge. Only messages from our own frame are honoured.
  useEffect(() => {
    const onMessage = async (event: MessageEvent) => {
      if (!frame.current || event.source !== frame.current.contentWindow) return
      const msg = event.data as FromWidget
      if (!msg || typeof msg !== 'object') return

      if (msg.type === 'height') {
        setHeight(Math.min(Math.max(msg.height + 8, 120), 2400))
      } else if (msg.type === 'error') {
        setFailed(msg.message)
      } else if (msg.type === 'fetch') {
        // The widget names a path; the host resolves it *relative to the file
        // it was given* and refuses to leave that directory. The frame has no
        // credentials of its own, so this is the only way it reaches anything,
        // and confining it here is what keeps a widget from reading the rest of
        // the project.
        const base = path.split('/').slice(0, -1).join('/')
        const wanted = `${base}/${msg.path}`.replace(/\/+/g, '/')
        const normalised = new URL(wanted, 'file:///').pathname.slice(1)
        const reply = (body: string | null, error?: string) =>
          frame.current?.contentWindow?.postMessage(
            { type: 'asset', id: msg.id, body, error },
            '*',
          )
        if (!normalised.startsWith(base)) {
          reply(null, 'outside this package')
          return
        }
        try {
          if (msg.binary) {
            // Fetched here, with credentials, and handed over as bytes. The
            // frame turns them into a blob URL of its own.
            const response = await fetch(
              `/api/assets/raw?path=${encodeURIComponent(normalised)}`,
              { credentials: 'same-origin' },
            )
            if (!response.ok) throw new Error(`asset ${response.status}`)
            const buffer = await response.arrayBuffer()
            frame.current?.contentWindow?.postMessage(
              { type: 'asset', id: msg.id, body: buffer },
              '*',
              [buffer],
            )
            return
          }
          const asset = await readAsset(normalised)
          reply('content' in asset ? (asset.content as string) : null)
        } catch (err) {
          reply(null, err instanceof Error ? err.message : String(err))
        }
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [path])

  if (failed) {
    return (
      <div className="widget-failed">
        <p className="error">This widget could not run: {failed}</p>
        <p className="muted small">
          {widget.title} · {widget.name}
        </p>
      </div>
    )
  }
  if (srcDoc === null) return <p className="muted">Loading {widget.title}…</p>

  return (
    <iframe
      ref={frame}
      className="widget-frame"
      title={widget.title}
      // No allow-same-origin: the frame gets an opaque origin and cannot reach
      // this page's session. Do not add it to "fix" a widget -- fix the widget.
      sandbox="allow-scripts"
      srcDoc={srcDoc}
      style={{ height }}
    />
  )
}
