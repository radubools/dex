// The cost dashboard: spend by skill, and the token split. The API is answered
// in the browser from fixtures.
//
//   npx playwright test --config web/playwright.config.ts costs
import { readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'

/** A day `n` days back, as the server names its buckets: fixtures stay inside the week shown. */
const day = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString().slice(0, 10) + 'T00:00:00+00:00'

const point = (bucket: string, series: string, cost: number, thinking: number) => ({
  bucket, series, cost, tasks: 1, input: 100, cacheRead: 9000, cacheWrite: 500, output: thinking * 2, thinking,
})
const fixture = {
  totals: { day: 1, week: 7, month: 30, all_time: 90 },
  series: [
    point(day(2), 'game-story', 5, 400),
    point(day(2), '(no skill)', 1, 100),
    point(day(1), 'game-story', 2, 200),
  ],
  coverage: { spend: 8, with_tokens: 6, inferred: 2 },
  period: 'week', granularity: 'day', group: 'skill',
}

/** `COSTS_JSON` swaps in a dump of real responses, to look at the real thing. */
const real = process.env.COSTS_JSON ? JSON.parse(readFileSync(process.env.COSTS_JSON, 'utf8')) : null

/** The drill-down's answer: whatever was asked for, recorded, and `drill` tasks paged by `limit`. */
type Asked = { bucket: string | null; series: string[]; page: number; sort: string | null; source: string | null }
const drillTask = (n: number) => ({
  id: `task-${n}`, title: `Task ${n}`, slug: `task-${n}`, project: 'alpha', model: 'claude-x', state: 'succeeded',
  threadId: null, createdAt: 1_793_000_000 + n, finishedAt: null, cost: 10 - n * 0.5, tokens: 1000 * n,
})

async function open(page: Page, costs: unknown = fixture, earliest?: string, drill: { total: number; asked?: Asked[]; project?: boolean } = { total: 3 }) {
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url())
    const p = url.pathname
    const q = url.searchParams
    if (p === '/api/costs/tasks') {
      const page = Number(q.get('page') ?? 0), limit = Number(q.get('limit') ?? 25)
      drill.asked?.push({ bucket: q.get('bucket'), series: q.getAll('series'), page, sort: q.get('sort'), source: q.get('source') })
      const all = Array.from({ length: drill.total }, (_, i) => drillTask(i))
      return route.fulfill({ json: {
        tasks: all.slice(page * limit, page * limit + limit), total: drill.total,
        cost: all.reduce((a, t) => a + t.cost, 0), tokens: 0, page, limit, sort: q.get('sort'), source: 'usage',
      } })
    }
    if (p.startsWith('/api/tasks/task-')) {
      const id = p.split('/')[3]
      if (p.endsWith('/events')) return route.fulfill({ json: { taskId: id, events: [] } })
      if (p.endsWith('/messages')) return route.fulfill({ json: { messages: [] } })
      if (p.endsWith(id)) return route.fulfill({ json: { task: { ...drillTask(Number(id.slice(5))), problem: 'p',
        activity: null, startedAt: null, error: null, costUsd: 1, turns: 1, outputDir: '', artifacts: [], pending: [],
        attempt: 1, parentId: null, sessionId: null, resumedFrom: null, outputSlug: id, costIsEstimate: false,
        canResume: false, canRerun: false, canRestart: false, canPause: false } } })
      return route.fulfill({ json: { taskId: id, events: [], messages: [] } })
    }
    const thread = { id: 't1', title: 'Main', project: 'alpha', kind: 'chat', createdAt: 0, updatedAt: 0, messageCount: 0, taskIds: [] }
    if (drill.project && p === '/api/threads') return route.fulfill({ json: { threads: [thread] } })
    if (drill.project && p === '/api/threads/t1') return route.fulfill({ json: { thread: { ...thread, messages: [] }, tasks: [] } })
    const body =
      p === '/api/costs' ? { ...(real?.[`${q.get('period')}/${q.get('granularity')}/${q.get('group')}`] ?? costs),
          period: q.get('period'), granularity: q.get('granularity'), offset: Number(q.get('offset') ?? 0),
          earliest: earliest ?? null, projects: ['alpha', 'beta', 'gamma'] }
      : p === '/api/auth/me' ? { state: 'open', user: null, googleEnabled: false, passwordEnabled: false }
      : p === '/api/health' ? { ok: true, workspace: '', assetsDir: '', model: 'test', concurrency: 1 }
      : p === '/api/settings' ? { settings: { auto_approve: false }, costs: fixture.totals, tokens: { input: 0, cache_read: 0, cache_write: 0, output: 0, thinking: 0, visible: 0, counted: 0, with_cost: 0 }, models: [], efforts: [] }
      // Steps back are recorded per project, so a test of back needs one.
      : p === '/api/projects' ? (drill.project
          ? { projects: [{ slug: 'alpha', name: 'Alpha', description: '', createdAt: 0, updatedAt: 0, threads: 0, tasks: 0 }], default: 'alpha' }
          : { projects: [], default: null })
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
  await page.getByRole('button', { name: 'Cost dashboard' }).click()
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
  await expect(panel.locator('.legend li').nth(1)).toContainText('game-story')
})

test('the largest series leads the legend and Other closes it', async ({ page }) => {
  const series = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i'].map((n, i) =>
    point('2026-10-29T00:00:00+00:00', n, i + 1, 1))
  await open(page, { ...fixture, series })
  const legend = page.getByRole('dialog', { name: 'Cost dashboard' }).locator('.legend li')
  await expect(legend.first()).toContainText('All series')
  await expect(legend.nth(1)).toContainText('i')
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

test('hovering a bar lists its values and leaving hides them', async ({ page }) => {
  await open(page, { ...fixture, series: [point(day(1), 'game-story', 5, 1), point(day(1), 'game-mesh', 1.5, 1)] })
  const panel = page.getByRole('dialog', { name: 'Cost dashboard' })
  await expect(panel.locator('.cost-chart .axis')).toHaveCount(0)

  const columns = panel.locator('.cost-column')
  await expect(columns).toHaveCount(7)
  await columns.nth(5).hover()
  const card = panel.getByRole('tooltip')
  await expect(card.locator('strong')).toHaveText('$6.50')
  await expect(card.locator('.cost-card-row').first()).toContainText('game-story')
  await expect(card.locator('.cost-card-row').last()).toContainText('$1.50')
  await card.evaluate((el) => {
    const box = el.getBoundingClientRect(), plot = el.parentElement!.getBoundingClientRect()
    if (box.left < plot.left - 1 || box.right > plot.right + 1) throw new Error('card outside the chart')
  })

  await columns.nth(0).hover()
  await expect(card).toContainText('Nothing spent')
  await page.mouse.move(5, 5)
  await expect(card).toHaveCount(0)
})

test.describe('on a touch screen', () => {
  test.use({ hasTouch: true })

  test('a tap keeps the card for 5 s, until another bar or a tap outside', async ({ page }) => {
      await open(page, { ...fixture, series: [point(day(1), 'game-story', 5, 1), point(day(3), 'game-mesh', 2, 1)] })
    await page.clock.install()
    const panel = page.getByRole('dialog', { name: 'Cost dashboard' })
    const columns = panel.locator('.cost-column')
    const card = panel.getByRole('tooltip')

    await columns.nth(5).tap()
    await expect(card.locator('strong')).toHaveText('$5.00')
    await page.clock.runFor(4500)
    await expect(card.locator('strong')).toHaveText('$5.00')

    // Another bar takes over and gets its own full 5 s.
    await columns.nth(3).tap()
    await expect(card.locator('strong')).toHaveText('$2.00')
    await page.clock.runFor(4500)
    await expect(card).toBeVisible()
    await page.clock.runFor(600)
    await expect(card).toHaveCount(0)

    await columns.nth(5).tap()
    await expect(card).toBeVisible()
    await panel.locator('.cost-grid').tap()
    await expect(card).toHaveCount(0)
  })
})

test('clicking series narrows to them, and All series brings the rest back', async ({ page }) => {
  await open(page)
  const panel = page.getByRole('dialog', { name: 'Cost dashboard' })
  const legend = (name: string) => panel.locator('.legend-item', { hasText: name })
  const bars = panel.locator('.cost-chart rect:not(.cost-column)')
  await expect(legend('All series')).toContainText('$8.00')
  await expect(panel.getByRole('row', { name: /All series/ })).toContainText('$8.00')
  await expect(bars).toHaveCount(3)

  await legend('(no skill)').click()
  await expect(bars).toHaveCount(1)
  await expect(legend('(no skill)')).toHaveAttribute('aria-pressed', 'true')
  await expect(legend('game-story')).toHaveClass(/off/)
  await expect(legend('All series')).toHaveClass(/off/)

  // More than one, from the table this time, which stays in step.
  await panel.getByRole('row', { name: /game-story/ }).click()
  await expect(bars).toHaveCount(3)
  await legend('game-story').click()
  await expect(bars).toHaveCount(1)

  await legend('All series').click()
  await expect(bars).toHaveCount(3)
  await expect(legend('All series')).toHaveAttribute('aria-pressed', 'true')

  // The last one off is the same as all.
  await legend('game-story').click()
  await legend('game-story').click()
  await expect(bars).toHaveCount(3)
})

test('token kinds can be picked too, and a group in the table narrows them', async ({ page }) => {
  await open(page)
  const panel = page.getByRole('dialog', { name: 'Cost dashboard' })
  await panel.getByRole('button', { name: 'Tokens' }).click()
  const bars = panel.locator('.cost-chart rect:not(.cost-column)')
  const before = await bars.count()
  await panel.locator('.legend-item', { hasText: 'Reasoning' }).click()
  await expect(bars).toHaveCount(2)
  await panel.locator('.legend-item', { hasText: 'All series' }).click()
  await expect(bars).toHaveCount(before)
  await panel.getByRole('row', { name: /no skill/ }).click()
  await expect(bars).toHaveCount(3)
})

test('Code lines and Asset lines show summed lines, not cost', async ({ page }) => {
  await open(page, { ...fixture, lines: [
    { bucket: '2026-10-29T00:00:00+00:00', series: 'game-story', code: 1500, assets: 40 },
    { bucket: '2026-10-30T00:00:00+00:00', series: '(no skill)', code: 500, assets: 2 },
  ] })
  await page.getByRole('button', { name: 'Code lines' }).click()
  await expect(page.locator('.cost-cell', { hasText: 'Code lines' }).locator('.cost-value')).toHaveText('2.0k')
  await expect(page.locator('.cost-cell', { hasText: 'Asset lines' }).locator('.cost-value')).toHaveText('42')
  await expect(page.locator('.legend-item', { hasText: 'game-story' }).locator('.legend-value')).toHaveText('1.5k')
  await page.getByRole('button', { name: 'Asset lines' }).click()
  await expect(page.locator('.legend-item', { hasText: 'game-story' }).locator('.legend-value')).toHaveText('40')
  await expect(page.locator('.legend-item', { hasText: 'game-story' })).not.toContainText('$')
})

test('clicking a bar lists the tasks it sums, a page at a time, and a task opens its view', async ({ page }) => {
  const asked: Asked[] = []
  await open(page, { ...fixture, series: [point(day(1), 'game-story', 5, 1), point(day(1), 'game-mesh', 1, 1)] },
             undefined, { total: 23, asked, project: true })
  const panel = page.getByRole('dialog', { name: 'Cost dashboard' })
  const list = panel.getByRole('region', { name: 'Tasks in this slice' })
  await expect(list).toHaveCount(0)

  // The bottom of the bar is the largest series, game-story.
  // Measured at each click: the list below can scroll the pane.
  const column = panel.locator('.cost-column').nth(5)
  const at = async (where: 'top' | 'bottom') => {
    await column.scrollIntoViewIfNeeded()
    const box = (await column.boundingBox())!
    await page.mouse.click(box.x + box.width / 2, where === 'top' ? box.y + 3 : box.y + box.height - 3)
  }
  await at('bottom')
  await expect(list).toBeVisible()
  await expect.poll(() => asked.at(-1)).toMatchObject({ bucket: day(1), series: ['game-story'], page: 0, sort: 'cost', source: 'usage' })
  await expect(list).toContainText('game-story')
  await expect(list).toContainText('23 tasks')
  await expect(list.locator('tbody tr')).toHaveCount(10)
  await expect(list.locator('tbody tr').first()).toContainText('$10.00')
  await expect(panel.locator('.cost-chart g.dim')).toHaveCount(6)
  if (process.env.COSTS_SHOTS) await page.screenshot({ path: `${process.env.COSTS_SHOTS}/drill.png`, fullPage: true })

  // Pages, then a sort, which starts again from the first page.
  await list.getByRole('button', { name: 'Next page of tasks' }).click()
  await expect(list).toContainText('Page 2 of 3')
  await expect.poll(() => asked.at(-1)?.page).toBe(1)
  await list.getByRole('button', { name: 'Tokens' }).click()
  await expect.poll(() => asked.at(-1)).toMatchObject({ sort: 'tokens', page: 0 })

  // The top of the tallest bar is its last series; Whole bar is every series.
  await at('top')
  await expect.poll(() => asked.at(-1)).toMatchObject({ bucket: day(1), series: ['game-mesh'] })
  await list.getByRole('button', { name: 'Whole bar' }).click()
  await expect.poll(() => asked.at(-1)).toMatchObject({ bucket: day(1), series: [] })
  await expect(list).toContainText('all series')
  await expect(list.getByRole('button', { name: 'Whole bar' })).toHaveCount(0)
  // A segment clicked again clears it, and so does ✕.
  await at('bottom')
  await at('bottom')
  await expect(list).toHaveCount(0)
  await at('bottom')
  await list.getByRole('button', { name: 'Clear selection' }).click()
  await expect(list).toHaveCount(0)

  // A task opens its own view; back returns to the dashboard and its slice.
  await at('bottom')
  await list.getByRole('button', { name: 'Task 2' }).click()
  await expect(page.getByRole('dialog', { name: 'Cost dashboard' })).toHaveCount(0)
  await expect(page.locator('.task-panel')).toContainText('Task 2')
  await page.goBack()
  await expect(page.getByRole('dialog', { name: 'Cost dashboard' }).getByRole('region', { name: 'Tasks in this slice' }))
    .toContainText('game-story')
})

test('a legend pick narrows the whole bar to the series left on', async ({ page }) => {
  const asked: Asked[] = []
  await open(page, { ...fixture, series: [point(day(1), 'game-story', 5, 1), point(day(1), 'game-mesh', 1, 1)] },
             undefined, { total: 1, asked })
  const panel = page.getByRole('dialog', { name: 'Cost dashboard' })
  await panel.locator('.legend-item', { hasText: 'game-mesh' }).click()
  const box = (await panel.locator('.cost-column').nth(5).boundingBox())!
  // game-mesh alone is the tallest bar now: the whole bar, from the list.
  await page.mouse.click(box.x + box.width / 2, box.y + box.height - 3)
  await panel.getByRole('button', { name: 'Whole bar' }).click()
  await expect.poll(() => asked.at(-1)).toMatchObject({ series: ['game-mesh'] })
})
