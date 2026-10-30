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

async function open(page: Page, costs: unknown = fixture, earliest?: string) {
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url())
    const p = url.pathname
    const q = url.searchParams
    const body =
      p === '/api/costs' ? { ...(real?.[`${q.get('period')}/${q.get('granularity')}/${q.get('group')}`] ?? costs),
          period: q.get('period'), granularity: q.get('granularity'), offset: Number(q.get('offset') ?? 0),
          earliest: earliest ?? null, projects: ['alpha', 'beta', 'gamma'] }
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
  await panel.getByRole('button', { name: 'All kinds' }).click()
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

const hours = (n: number) => new Date(Date.now() - n * 3_600_000).toISOString().slice(0, 13) + ':00:00+00:00'

test('every bucket of a period is on the axis, and hours read as hours', async ({ page }) => {
  await open(page, { ...fixture, series: [point(hours(10), 'game-story', 1, 1), point(hours(2), 'game-story', 1, 1)] })
  const panel = page.getByRole('dialog', { name: 'Cost dashboard' })
  await panel.getByRole('button', { name: '24h', exact: true }).click()
  await expect(panel.locator('.segmented button', { hasText: /^(hour|day|week|month)$/ })).toHaveText(['hour'])
  await expect(panel.locator('.cost-chart g')).toHaveCount(24)
  await expect(panel.locator('.cost-chart .tick').last()).toHaveText(/^\d\d:00$/)
  const ticks = await panel.locator('.cost-chart .tick').allTextContents()
  expect(new Set(ticks).size).toBe(ticks.length)
})

test('a token kind on its own follows the group, and the cards follow the period', async ({ page }) => {
  await open(page)
  const panel = page.getByRole('dialog', { name: 'Cost dashboard' })
  await panel.getByRole('button', { name: 'Tokens' }).click()
  await expect(panel.locator('.cost-label', { hasText: 'Reasoning' })).toBeVisible()
  await expect(panel.locator('.legend li', { hasText: 'Cache read' })).toHaveCount(0)
  await panel.locator('.segmented').last().getByRole('button', { name: 'Reasoning' }).click()
  await expect(panel.locator('.legend li').first()).toContainText('game-story')
})

test('the largest series leads the legend and Other closes it', async ({ page }) => {
  const series = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i'].map((n, i) =>
    point('2026-10-29T00:00:00+00:00', n, i + 1, 1))
  await open(page, { ...fixture, series })
  const legend = page.getByRole('dialog', { name: 'Cost dashboard' }).locator('.legend li')
  await expect(legend.first()).toContainText('i')
  await expect(legend.last()).toContainText('Other')
})

test('several projects can be chosen, except when grouping by project', async ({ page }) => {
  await open(page)
  const panel = page.getByRole('dialog', { name: 'Cost dashboard' })
  const asked: (string | null)[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/costs')) asked.push(new URL(r.url()).searchParams.get('projects'))
  })
  await panel.locator('.cost-filter summary').click()
  await panel.getByRole('checkbox', { name: 'alpha' }).check()
  await panel.getByRole('checkbox', { name: 'gamma' }).check()
  await expect(panel.locator('.cost-filter summary')).toHaveText('2 projects')
  await expect.poll(() => asked.at(-1)).toBe('alpha,gamma')

  await panel.getByRole('button', { name: 'Project', exact: true }).click()
  await expect(panel.locator('.cost-filter')).toHaveCount(0)
  await expect.poll(() => asked.at(-1)).toBeNull()

  await panel.getByRole('button', { name: 'Model', exact: true }).click()
  await expect(panel.locator('.cost-filter summary')).toHaveText('2 projects')
  await expect.poll(() => asked.at(-1)).toBe('alpha,gamma')
})

test('a week of hours labels its days', async ({ page }) => {
  await open(page, { ...fixture, series: [point(hours(30), 'game-story', 1, 1)] })
  const panel = page.getByRole('dialog', { name: 'Cost dashboard' })
  await panel.locator('.segmented button', { hasText: /^hour$/ }).click()
  const ticks = await panel.locator('.cost-chart .tick').allTextContents()
  expect(ticks.length).toBeGreaterThan(4)
  expect(new Set(ticks).size).toBe(ticks.length)
})

test('the arrows page through earlier periods and stop at the ends', async ({ page }) => {
  await open(page, { ...fixture, series: [] }, hours(24 * 16))
  const panel = page.getByRole('dialog', { name: 'Cost dashboard' })
  const asked: (string | null)[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/costs')) asked.push(new URL(r.url()).searchParams.get('offset'))
  })
  const older = panel.getByRole('button', { name: 'Previous period' })
  const newer = panel.getByRole('button', { name: 'Next period' })
  await expect(newer).toBeDisabled()
  const range = await panel.locator('.cost-range').textContent()

  await older.click()
  await expect.poll(() => asked.at(-1)).toBe('1')
  await expect(panel.locator('.cost-range')).not.toHaveText(range!)
  await older.click()
  await expect.poll(() => asked.at(-1)).toBe('2')
  await expect(older).toBeDisabled()
  await newer.click()
  await expect.poll(() => asked.at(-1)).toBe('1')

  await panel.getByRole('button', { name: 'All', exact: true }).click()
  await expect(older).toHaveCount(0)
  await expect.poll(() => asked.at(-1)).toBe('0')
})
