// A dex server that does not answer: the page offers the server list rather
// than staying blank, and carries on if the server comes back. The API is
// answered in the browser from fixtures.
//
//   npx playwright test --config web/playwright.config.ts server-gate
import { expect, test, type Page } from '@playwright/test'

const info = {
  default: 'http://localhost:4317', current: 'http://gone.local:4317', custom: true, cookie: 'dex_api',
  presets: [
    { label: 'This machine', origin: 'http://localhost:4317' },
    { label: 'Gone', origin: 'http://gone.local:4317' },
  ],
}

async function picker(page: Page) {
  await page.route('**/__dex/api-server', (route) => route.fulfill({ json: info }))
  await page.route('**/__dex/api-server/probe**', (route) => {
    const origin = new URL(route.request().url()).searchParams.get('origin')
    return route.fulfill({ json: { origin, status: origin?.includes('gone') ? null : 200 } })
  })
}

test('a server that never answers gets the server list', async ({ page }) => {
  await picker(page)
  await page.route('**/api/**', () => {})  // hangs, as an unreachable address does
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'This dex server is not answering' })).toBeVisible({ timeout: 8000 })
  await expect(page.getByRole('button', { name: /This machine/ })).toBeEnabled()
  await expect(page.getByRole('button', { name: /Gone/ })).toBeDisabled()

  await page.getByRole('button', { name: /This machine/ }).click()
  await expect.poll(async () => (await page.context().cookies()).find((c) => c.name === 'dex_api')?.value)
    .toBe(encodeURIComponent('http://localhost:4317'))
})

test('the page carries on when the server comes back', async ({ page }) => {
  await picker(page)
  let up = false
  await page.route('**/api/**', (route) => {
    if (!up) return route.fulfill({ status: 502, body: '' })
    const p = new URL(route.request().url()).pathname
    return p === '/api/auth/me'
      ? route.fulfill({ json: { state: 'anonymous', user: null, googleEnabled: false, passwordEnabled: true } })
      : route.fulfill({ json: { tasks: [], threads: [], projects: [], default: 'alpha', settings: {} } })
  })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'This dex server is not answering' })).toBeVisible()
  up = true
  await expect(page.getByRole('heading', { name: 'This dex server is not answering' })).toBeHidden({ timeout: 8000 })
  await expect(page.getByText('dex server', { exact: true })).toBeVisible()  // the sign-in page's
})
