import { defineConfig } from '@playwright/test'

/**
 * Widget tests.
 *
 *   node_modules/.bin/playwright test --config widgets/playwright.config.ts
 *   node_modules/.bin/playwright test --config widgets/playwright.config.ts pose-3d
 *
 * The server is a plain static file server over the repository root, so a test
 * can reach `/widgets/<name>/dist/index.js` and any real file under `assets/`.
 * Nothing here talks to dex: a widget must work from the file it is handed, and
 * a test that needs the whole application running is testing the wrong thing.
 *
 * `cwd` is pinned so the server starts the same way wherever you ran the
 * command from -- a task runs it from its own package directory. A spec must do
 * the same for its fixtures: resolve them from `import.meta.url`, never from a
 * bare relative path, which is read against whatever cwd happened to be.
 */
export default defineConfig({
  testDir: '.',
  testMatch: '**/test/*.spec.ts',
  fullyParallel: true,
  reporter: process.env.CI ? 'list' : [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4319',
    // Kept on failure only: a passing widget test should leave nothing behind.
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'node widgets/serve.mjs',
    url: 'http://127.0.0.1:4319/widgets/harness.html?widget=none',
    reuseExistingServer: true,
    cwd: '..',
    timeout: 20_000,
  },
})
