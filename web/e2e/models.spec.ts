// The model pickers: Claude's models and the Antigravity ones, grouped by whose
// subscription runs them (design/27). The API is answered in the browser.
//
//   npx playwright test --config web/playwright.config.ts models
import { expect, test, type Page } from '@playwright/test'

const models = [
  { id: 'claude-opus-5-5', label: 'Opus 5.5', note: 'most capable', provider: 'claude', available: true },
  { id: 'agy:gemini-4-argon-high', label: 'Gemini 4 Argon (High)', note: 'Google subscription, via Antigravity',
    provider: 'antigravity', available: true },
  { id: 'agy:gemini-3.8-flash-low', label: 'Gemini 3.8 Flash (Low)', note: 'Google subscription, via Antigravity',
    provider: 'antigravity', available: false, unavailable: 'sign in: run `agy`, then Google OAuth' },
]

async function open(page: Page, saved: Record<string, unknown>) {
  await page.route('**/api/**', (route) => {
    const p = new URL(route.request().url()).pathname
    const body =
      p === '/api/auth/me' ? { state: 'open', user: null, googleEnabled: false, passwordEnabled: false }
      : p === '/api/health' ? { ok: true, workspace: '', assetsDir: '', model: 'claude-opus-5-5', concurrency: 1 }
      : p === '/api/settings' && route.request().method() === 'PUT'
        ? { settings: { ...saved, ...route.request().postDataJSON() } }
      : p === '/api/settings' ? { settings: saved, costs: { day: 0, week: 0, month: 0, all_time: 0 },
          tokens: { input: 0, cache_read: 0, cache_write: 0, output: 0, thinking: 0, visible: 0, counted: 0, with_cost: 0 },
          models, efforts: [], project: 'alpha', defaultModel: 'claude-opus-5-5', defaultEffort: 'high' }
      : p === '/api/projects' ? { projects: [], default: null }
      : p === '/api/threads' ? { threads: [] }
      : p === '/api/tasks' ? { tasks: [] }
      : p === '/api/packages' ? { packages: [] }
      : { ok: true }
    return route.fulfill({ json: body })
  })
  await page.addInitScript(() => {
    class Quiet { close() {} addEventListener() {} }
    ;(window as unknown as { EventSource: unknown }).EventSource = Quiet
  })
  await page.goto('/')
  await page.getByRole('button', { name: /^Settings/ }).click()
  return page.getByRole('dialog', { name: 'Settings' })
}

test('models are grouped by subscription, and one that cannot run says why', async ({ page }) => {
  const menu = await open(page, { model: 'agy:gemini-4-argon-high' })
  const picker = menu.locator('select').filter({ has: page.locator('option', { hasText: /^Default/ }) })
  await expect(picker).toHaveValue('agy:gemini-4-argon-high')
  await expect(picker.locator('optgroup')).toHaveCount(2)
  await expect(picker.locator('optgroup').nth(0)).toHaveAttribute('label', 'Claude subscription')
  await expect(picker.locator('optgroup').nth(1)).toHaveAttribute('label', 'Google subscription (Antigravity)')
  const unavailable = picker.locator('option[value="agy:gemini-3.8-flash-low"]')
  await expect(unavailable).toBeDisabled()
  await expect(unavailable).toContainText('sign in')

  const request = page.waitForRequest((r) => r.url().endsWith('/api/settings') && r.method() === 'PUT')
  await picker.selectOption('claude-opus-5-5')
  expect((await request).postDataJSON()).toEqual({ model: 'claude-opus-5-5' })
})
