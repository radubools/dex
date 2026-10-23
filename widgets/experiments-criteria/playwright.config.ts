import { defineConfig } from '@playwright/test'
import { fileURLToPath } from 'node:url'

/**
 * The installation check for `experiments-criteria`.
 *
 *   node_modules/.bin/playwright test --config widgets/experiments-criteria/playwright.config.ts
 *
 * Only one server: the static harness. Unlike the skill's own config this does
 * **not** start `serve_standalone.py`, because the whole point is to talk to the
 * `/api/sql` router that `dex-sql-api/install.py` put into `src/dex/api.py` —
 * running on dex's own port, under pm2. If these tests fail with "cannot reach
 * http://127.0.0.1:4317/api/sql", the router is not installed, and the skill's
 * own suite is the one to run instead.
 */
const REPO = fileURLToPath(new URL('../../', import.meta.url))

export default defineConfig({
  testDir: fileURLToPath(new URL('./test/', import.meta.url)),
  testMatch: '*.spec.ts',
  fullyParallel: true,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4319',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'node widgets/serve.mjs',
    url: 'http://127.0.0.1:4319/widgets/harness.html?widget=none',
    reuseExistingServer: true,
    cwd: REPO,
    timeout: 20_000,
  },
})
