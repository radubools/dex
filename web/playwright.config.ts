// Browser tests of the web UI (web/e2e). They run against the Vite dev
// server (`npm run dev:ui`, port 4318 under pm2) with the API answered in the
// browser from fixtures: no sign-in, no server state touched.
//   npx playwright test --config web/playwright.config.ts
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  use: {
    baseURL: process.env.DEX_UI_URL ?? 'http://localhost:4318',
    viewport: { width: 1280, height: 800 },
  },
})
