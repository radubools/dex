import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // Bind on 0.0.0.0 so the dev server is reachable from a phone on the LAN.
    host: true,
    port: 4318,
    proxy: { '/api': 'http://localhost:4317' },
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
  build: { outDir: 'dist', sourcemap: true },
})
