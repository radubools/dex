import { defineConfig } from '@playwright/test'
import { fileURLToPath } from 'node:url'

/**
 * This widget's tests.
 *
 *   node_modules/.bin/playwright test --config \
 *     skills/postgres-experiments-state/<version>/widgets/experiments-criteria/playwright.config.ts
 *
 * Why a config of its own rather than `widgets/playwright.config.ts`: that one
 * sets `testDir: '.'`, which resolves to the top-level `widgets/` directory —
 * and since widgets moved inside skills, nothing lives there but the builder,
 * the harness and this server. It discovers zero specs today, this one's and
 * every other skill's alike. Rather than edit shared tooling from a project
 * task, this points `testDir` at the directory it sits in.
 *
 * Everything else is copied from it deliberately: same port, same harness URL,
 * same `cwd` two levels up so the static server's root is the repository.
 */
const REPO = fileURLToPath(new URL('../../../../../', import.meta.url))

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
  // Two servers, both reused if already up. The second is the staged `/api/sql`
  // router on its own uvicorn: the live tests need a real endpoint answering
  // real CORS headers, and starting it here rather than from a `beforeAll` is
  // what stops several parallel workers racing for port 4399 — the first binds,
  // the rest fail, and the losers' cleanup then dies with `kill ESRCH`.
  webServer: [
    {
      command: 'node widgets/serve.mjs',
      url: 'http://127.0.0.1:4319/widgets/harness.html?widget=none',
      reuseExistingServer: true,
      cwd: REPO,
      timeout: 20_000,
    },
    {
      command: '.venv/bin/python3 assets/construction/dex-sql-api/serve_standalone.py --port 4399',
      url: 'http://127.0.0.1:4399/api/sql?sql=SELECT%201',
      reuseExistingServer: true,
      cwd: REPO,
      timeout: 40_000,
    },
  ],
})
