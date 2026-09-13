import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// This config runs in Node, but the web workspace has no `@types/node` and
// does not need one for a handful of env reads. Reached through globalThis so
// there is no ambient declaration to collide with if those types arrive later.
const env: Record<string, string | undefined> =
  (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env ?? {}

export default defineConfig({
  plugins: [react()],
  server: {
    // Bind on 0.0.0.0 so the dev server is reachable from a phone on the LAN.
    host: true,
    port: 4318,
    proxy: {
      '/api': 'http://localhost:4317',
      // Built widget bundles are served by the Python server, not by Vite.
      // Without this the dev server answers with its SPA fallback, the frame
      // imports index.html, and the widget dies on `Unexpected token '<'` --
      // which looks like a bug in the widget and is not.
      '/widgets': 'http://localhost:4317',
    },
    // Vite rejects requests whose Host header it does not recognise (a
    // DNS-rebinding guard). Reaching the dev server over Tailscale or by
    // machine name means the Host is not localhost, so those names have to be
    // listed. `.ts.net` covers any tailnet MagicDNS name; `.local` covers
    // Bonjour. This is the dev server only — the Python server does no such
    // filtering, so a production build served from it needs none of this.
    allowedHosts: ['.ts.net', '.local', 'macbook-pro-4', 'mac'],
    watch: {
      // Vite only watches its root (`web/`), so the Python sources and the
      // generated assets are already out of scope. `dist/` is not — without
      // this, a production build would reload the dev server mid-edit.
      ignored: ['**/dist/**', '**/node_modules/**'],
    },
  },
  // `vite preview` serves the build. It is what the container's `dex-web`
  // process runs, so its settings come from the environment rather than being
  // fixed here — a deployment's hostname and API origin are not known when
  // this file is written.
  preview: {
    host: true,
    port: Number(env.DEX_WEB_PORT ?? 4318),
    proxy: {
      '/api': env.DEX_API_ORIGIN ?? 'http://127.0.0.1:4317',
      // Same reason as the dev server above: widgets are the API server's.
      '/widgets': env.DEX_API_ORIGIN ?? 'http://127.0.0.1:4317',
    },
    // Same DNS-rebinding guard as the dev server above. `DEX_ALLOWED_HOSTS` is
    // a comma-separated list; unset means allow any Host, which is correct
    // behind a load balancer that has already matched the host itself and
    // wrong if this port is exposed to the internet directly.
    allowedHosts: env.DEX_ALLOWED_HOSTS
      ? env.DEX_ALLOWED_HOSTS.split(',').map((h) => h.trim()).filter(Boolean)
      : true,
  },
  build: { outDir: 'dist', sourcemap: true },
})
