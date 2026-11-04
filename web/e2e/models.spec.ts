// The model pickers: a provider (Claude, Gemini through Antigravity, OpenCode Go),
// then one of its models (design/27, 28). The API is answered in the browser.
//
//   npx playwright test --config web/playwright.config.ts models
import { expect, test, type Page } from '@playwright/test'

const models = [
  { id: 'claude-opus-5-5', label: 'Opus 5.5', note: 'most capable', provider: 'claude', available: true },
  { id: 'agy:gemini-4-argon-high', label: 'Gemini 4 Argon (High)', note: 'Google subscription, via Antigravity',
    provider: 'antigravity', available: true },
  { id: 'agy:gemini-3.8-flash-low', label: 'Gemini 3.8 Flash (Low)', note: 'Google subscription, via Antigravity',
    provider: 'antigravity', available: false, unavailable: 'sign in: run `agy`, then Google OAuth' },
  { id: 'oc:qwen3.8-flash', label: 'Qwen3.8 Flash', note: 'OpenCode Go subscription', provider: 'opencode', available: true },
  { id: 'oc:glm-5.3', label: 'GLM-5.3', note: 'OpenCode Go subscription', provider: 'opencode', available: true },
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

test('a provider, then its models; one that cannot run says why', async ({ page }) => {
  const menu = await open(page, { model: 'agy:gemini-4-argon-high' })
  const provider = menu.getByRole('combobox', { name: 'Model: provider' })
  const model = menu.getByRole('combobox', { name: 'Model: model' })
  await expect(provider.locator('option')).toHaveText(['Claude', 'Gemini', 'OpenCode'])
  await expect(provider).toHaveValue('antigravity')
  await expect(model).toHaveValue('agy:gemini-4-argon-high')
  await expect(model.locator('option')).toHaveCount(3) // the default, and Gemini's two
  const unavailable = model.locator('option[value="agy:gemini-3.8-flash-low"]')
  await expect(unavailable).toBeDisabled()
  await expect(unavailable).toContainText('sign in')

  // A provider switch picks its first model, Qwen3.8 Flash for OpenCode.
  let request = page.waitForRequest((r) => r.url().endsWith('/api/settings') && r.method() === 'PUT')
  await provider.selectOption('opencode')
  expect((await request).postDataJSON()).toEqual({ model: 'oc:qwen3.8-flash' })
  await expect(model.locator('option')).toHaveText(['Default (claude-opus-5-5)', /Qwen3.8 Flash/, /GLM-5.3/])

  request = page.waitForRequest((r) => r.url().endsWith('/api/settings') && r.method() === 'PUT')
  await model.selectOption('oc:glm-5.3')
  expect((await request).postDataJSON()).toEqual({ model: 'oc:glm-5.3' })

  // The project's own picker works the same way.
  await expect(menu.getByRole('combobox', { name: 'Model for alpha: provider' })).toHaveValue('claude')
})
