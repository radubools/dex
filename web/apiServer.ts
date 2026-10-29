/**
 * Which dex API the web server forwards `/api` and `/widgets` to.
 *
 * The page always talks to its own origin, so its session cookie, the event
 * stream and widget frames work exactly as they do with one server. What
 * changes is where the dev (or preview) server sends those requests: the
 * address in the `dex_api` cookie, set from Settings, or the implicit one
 * (DEX_API_ORIGIN, else port 4317 on this machine) when there is none.
 *
 * Only an address on this machine, the LAN or the tailnet is honoured. This
 * server listens on every interface, and a cookie that could name any host
 * would make it an open relay for whoever can reach it.
 */
import type { Plugin, ProxyOptions } from 'vite'
import presets from './api-servers.json'

export const API_COOKIE = 'dex_api'

type Req = { headers: Record<string, string | string[] | undefined>; url?: string }
type Res = {
  statusCode: number
  setHeader: (name: string, value: string) => void
  end: (body?: string) => void
}

/** `http(s)://host:port` when `value` names an allowed server, else null. */
export function allowedOrigin(value: string | undefined | null): string | null {
  if (!value) return null
  let url: URL
  try {
    url = new URL(value)
  } catch {
    return null
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return null
  const host = url.hostname.replace(/^\[|\]$/g, '').toLowerCase()
  const ip = host.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/)?.slice(1).map(Number)
  const ok =
    host === 'localhost' || host === '::1' ||
    host.endsWith('.ts.net') || host.endsWith('.local') ||
    // A bare MagicDNS name, `macbook-pro-4`.
    /^[a-z0-9-]+$/.test(host) && !/^\d+$/.test(host) ||
    (ip !== undefined && (
      ip[0] === 127 || ip[0] === 10 ||
      (ip[0] === 172 && ip[1] >= 16 && ip[1] <= 31) ||
      (ip[0] === 192 && ip[1] === 168) ||
      // Tailscale's CGNAT range, 100.64.0.0/10.
      (ip[0] === 100 && ip[1] >= 64 && ip[1] <= 127)
    ))
  return ok ? url.origin : null
}

function cookie(req: Req, name: string): string | undefined {
  const raw = req.headers.cookie
  const header = Array.isArray(raw) ? raw.join('; ') : raw ?? ''
  for (const part of header.split(';')) {
    const at = part.indexOf('=')
    if (at !== -1 && part.slice(0, at).trim() === name) {
      try {
        return decodeURIComponent(part.slice(at + 1).trim())
      } catch {
        return undefined
      }
    }
  }
  return undefined
}

/** The API a request goes to: the chosen one when it is allowed, else the default. */
const chosen = (req: Req, fallback: string) => allowedOrigin(cookie(req, API_COOKIE)) ?? fallback

/**
 * `/api` or `/widgets` forwarded to the request's chosen API. Vite builds one
 * proxy per path with a fixed target; this hands every request its own, which
 * the proxy merges over the fixed one.
 */
export function apiProxy(fallback: string): ProxyOptions {
  return {
    target: fallback,
    changeOrigin: true,
    configure(proxy) {
      const web = proxy.web.bind(proxy) as (req: Req, res: unknown, ...rest: unknown[]) => void
      // The proxy reads its arguments by position from the end, so a trailing
      // `undefined` callback would be taken for the options and the target
      // lost: pass a callback only when there is one.
      ;(proxy as unknown as { web: typeof web }).web = (req, res, options = {}, ...cb) =>
        web(req, res, { ...(options as object), target: chosen(req, fallback) }, ...cb.filter(Boolean))
    },
  }
}

/**
 * `/__dex/api-server`: what Settings needs to draw the picker (the default,
 * the current choice, the presets), `/__dex/api-server/probe?origin=` to say
 * whether an address answers before it is chosen, and
 * `/__dex/api-server/reset` to forget the choice from the address bar.
 */
export function apiServerPlugin(fallback: string): Plugin {
  const handle = async (req: Req, res: Res, next: () => void) => {
    const url = new URL(req.url ?? '/', 'http://x')
    if (!url.pathname.startsWith('/__dex/api-server')) return next()
    // The way back from an address that stopped answering, when the page
    // that holds the picker cannot load: open this and start over.
    if (url.pathname === '/__dex/api-server/reset') {
      res.statusCode = 302
      res.setHeader('set-cookie', `${API_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`)
      res.setHeader('location', '/')
      return res.end()
    }
    res.setHeader('content-type', 'application/json')
    res.setHeader('cache-control', 'no-store')
    if (url.pathname === '/__dex/api-server/probe') {
      const origin = allowedOrigin(url.searchParams.get('origin'))
      if (!origin) {
        res.statusCode = 400
        return res.end(JSON.stringify({ error: 'Not an address on this machine, the LAN or the tailnet' }))
      }
      let status: number | null = null
      try {
        // 401 is an answer too: a dex that wants a sign-in is up.
        status = (await fetch(`${origin}/api/health`, { signal: AbortSignal.timeout(4000) })).status
      } catch {
        status = null
      }
      return res.end(JSON.stringify({ origin, status }))
    }
    const picked = allowedOrigin(cookie(req, API_COOKIE))
    res.end(JSON.stringify({
      default: fallback,
      current: picked ?? fallback,
      custom: picked !== null,
      cookie: API_COOKIE,
      presets: presets.servers,
    }))
  }
  return {
    name: 'dex-api-server',
    configureServer: (server) => { server.middlewares.use((req, res, next) => void handle(req as unknown as Req, res as unknown as Res, next)) },
    configurePreviewServer: (server) => { server.middlewares.use((req, res, next) => void handle(req as unknown as Req, res as unknown as Res, next)) },
  }
}
