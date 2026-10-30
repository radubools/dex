// The cost dashboard: spend by skill, and the token split. The API is answered
// in the browser from fixtures.
//
//   npx playwright test --config web/playwright.config.ts costs
import { readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'

const point = (bucket: string, series: string, cost: number, thinking: number) => ({
  bucket, series, cost, tasks: 1, input: 100, cacheRead: 9000, cacheWrite: 500, output: thinking * 2, thinking,
})
const fixture = {
  totals: { day: 1, week: 7, month: 30, all_time: 90 },
  series: [
    point('2026-10-29T00:00:00+00:00', 'game-story', 5, 400),
    point('2026-10-29T00:00:00+00:00', '(no skill)', 1, 100),
    point('2026-10-30T00:00:00+00:00', 'game-story', 2, 200),
  ],
  coverage: { spend: 8, with_tokens: 6, inferred: 2 },
  period: 'week', granularity: 'day', group: 'skill',
}

/** `COSTS_JSON` swaps in a dump of real responses, to look at the real thing. */
const real = process.env.COSTS_JSON ? JSON.parse(readFileSync(process.env.COSTS_JSON, 'utf8')) : null

async function open(page: Page, costs: unknown = fixture) {
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url())
    const p = url.pathname
    const q = url.searchParams
    const body =
      p === '/api/costs' ? (real ? real[`${q.get('period')}/${q.get('granularity')}/${q.get('group')}`] ?? costs : costs)
      : p === '/api/auth/me' ? { state: 'open', user: null, googleEnabled: false, passwordEnabled: false }
      : p === '/api/health' ? { ok: true, workspace: '', assetsDir: '', model: 'test', concurrency: 1 }
      : p === '/api/settings' ? { settings: { auto_approve: false }, costs: fixture.totals, tokens: { input: 0, cache_read: 0, cache_write: 0, output: 0, thinking: 0, visible: 0, counted: 0, with_cost: 0 }, models: [], efforts: [] }
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
  await page.getByRole('button', { name: /settings/i }).first().click()
  await page.getByRole('button', { name: /cost|spend/i }).first().click()
  await expect(page.getByRole('dialog', { name: 'Cost dashboard' })).toBeVisible()
}

test('spend breaks down by skill, tokens by kind', async ({ page }) => {
  await open(page)
  const panel = page.getByRole('dialog', { name: 'Cost dashboard' })
  await expect(panel.getByRole('rowheader', { name: 'game-story' })).toBeVisible()
  await expect(panel.getByRole('rowheader', { name: '(no skill)' })).toBeVisible()
  await expect(panel.getByText('attributed afterwards')).toBeVisible()
  if (process.env.COSTS_SHOTS) await page.screenshot({ path: `${process.env.COSTS_SHOTS}/cost.png`, fullPage: true })

  await panel.getByRole('button', { name: 'Tokens' }).click()
  for (const kind of ['Input', 'Cache read', 'Cache write', 'Output', 'Reasoning']) {
    await expect(panel.locator('.legend li', { hasText: kind })).toBeVisible()
  }
  if (process.env.COSTS_SHOTS) await page.screenshot({ path: `${process.env.COSTS_SHOTS}/tokens.png`, fullPage: true })
})

test('a server without the rollup still shows the panel', async ({ page }) => {
  const { coverage: _, ...older } = fixture
  await open(page, older)
  await expect(page.getByRole('dialog', { name: 'Cost dashboard' }).getByRole('rowheader', { name: 'game-story' })).toBeVisible()
})
