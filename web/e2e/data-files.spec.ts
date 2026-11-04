// Data files a page at a time, and a package's HTML in a frame (design/13).
// The API is answered in the browser from fixtures.
//
//   npx playwright test --config web/playwright.config.ts data-files
import { expect, test, type Page } from '@playwright/test'

const now = 1_760_000_000
const task = {
  id: 'task-a', threadId: 't1', title: 'Build the data', slug: 'data', problem: 'Make it.', state: 'succeeded',
  activity: null, createdAt: now, startedAt: now, finishedAt: now, error: null, costUsd: 0.1, turns: 3,
  outputDir: 'alpha/data', artifacts: [], pending: [], attempt: 1, parentId: null, sessionId: null, resumedFrom: null,
  outputSlug: 'data', project: 'alpha', model: null, costIsEstimate: false, canResume: false, canRerun: true,
  canRestart: false, canPause: false, canContinue: true, canArchive: true, priority: 0, canPrioritize: false,
}
const thread = { id: 't1', title: 'Data', project: 'alpha', kind: 'chat', createdAt: now, updatedAt: now, messageCount: 1, taskIds: ['task-a'] }

function table(q: URLSearchParams) {
  const offset = Number(q.get('offset')), limit = Number(q.get('limit')), total = 1050
  const sheet = q.get('path')?.endsWith('.xlsx') ? q.get('sheet') ?? 'One' : undefined
  return {
    columns: ['id', sheet ?? 'name'], total, offset, limit,
    rows: Array.from({ length: Math.min(limit, total - offset) }, (_, i) => [`row ${offset + i + 1}`, 'x']),
    ...(sheet ? { sheets: ['One', 'Two'], sheet } : {}),
  }
}

function answer(url: URL): unknown {
  const p = url.pathname, q = url.searchParams
  if (p === '/api/auth/me') return { state: 'open', user: null, googleEnabled: false, passwordEnabled: false }
  if (p === '/api/health') return { ok: true, workspace: '', assetsDir: '', model: 'test', concurrency: 1, manim: true, autoApprove: false, authenticated: true, running: 0, queued: 0 }
  if (p === '/api/settings') return { settings: { auto_approve: false }, costs: { day: 0, week: 0, month: 0, all_time: 0 } }
  if (p === '/api/tasks') return { tasks: [task] }
  if (p === '/api/tasks/task-a') return { task }
  if (p === '/api/tasks/task-a/messages') return { messages: [] }
  if (p === '/api/tasks/task-a/events') return { taskId: 'task-a', events: [] }
  if (p === '/api/projects') return { projects: [{ slug: 'alpha', name: 'Alpha', description: '', createdAt: now, updatedAt: now, threads: 1, tasks: 1 }], default: 'alpha' }
  if (p === '/api/threads') return { threads: [thread] }
  if (p === '/api/threads/t1') return { thread: { ...thread, messages: [{ id: 'm1', role: 'dex', kind: 'tasks', text: 'Started 1 task', data: { tasks: [task] }, ts: now }] }, tasks: [task] }
  if (p === '/api/packages') return { packages: [] }
  if (p === '/api/widgets/resolve') return { widget: null, widgets: {} }
  if (/\/skills$/.test(p)) return { skills: [] }
  if (p === '/api/assets/table' && q.get('path')?.endsWith('plain.json')) return null
  if (p === '/api/assets/table') return table(q)
  if (p === '/api/assets/page') return { url: '/api/assets/page/1-a/alpha/data/report.html' }
  if (p === '/api/assets') {
    const path = q.get('path') ?? ''
    if (path.endsWith('.json')) return { kind: 'file', path, ext: 'json', content: '{"plain": true}' }
    return { kind: 'dir', path, entries: ['rows.jsonl', 'book.xlsx', 'report.html', 'list.json', 'plain.json'].map((n) => ({ name: `${path}/${n}`, dir: false, kind: 'file', bytes: 10 })) }
  }
  return { ok: true }
}

async function open(page: Page, file: string, also?: () => Promise<unknown>) {
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/events') return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
    if (url.pathname.startsWith('/api/assets/page/')) return route.fulfill({ status: 200, contentType: 'text/html', body: '<h1 id="x">Report</h1><script>document.getElementById("x").textContent = "Ran"</script>' })
    const body = answer(url)
    if (body === null) return route.fulfill({ status: 422, contentType: 'application/json', body: '{"detail":"not a list of objects"}' })
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  })
  await also?.()  // the last route added answers first
  await page.goto('/?project=alpha&thread=t1')
  await page.locator('.task-chip', { hasText: 'Build the data' }).first().click()
  await page.getByRole('button', { name: /^Files/ }).click()
  await page.locator('.asset-row', { hasText: file }).click()
}

const first = (page: Page) => page.locator('.table-view tbody tr').first().locator('td').nth(1)

test('a data file moves through its pages and sizes', async ({ page }) => {
  await open(page, 'rows.jsonl')
  await expect(first(page)).toHaveText('row 1')
  await expect(page.locator('.table-view tbody tr')).toHaveCount(100)
  await expect(page.getByLabel('First page')).toBeDisabled()
  await expect(page.locator('.sheet-tabs')).toHaveCount(0)

  await page.getByLabel('Next page').click()
  await expect(first(page)).toHaveText('row 101')
  await page.getByLabel('Last page').click()
  await expect(first(page)).toHaveText('row 1001')
  await expect(page.getByText('Page 11 of 11')).toBeVisible()
  await expect(page.getByLabel('Next page')).toBeDisabled()
  await page.getByLabel('Previous page').click()
  await expect(first(page)).toHaveText('row 901')

  await page.getByLabel('Rows per page').selectOption('500')
  await expect(first(page)).toHaveText('row 501')
  await expect(page.locator('.table-view tbody tr')).toHaveCount(500)
  await page.getByLabel('First page').click()
  await expect(first(page)).toHaveText('row 1')
})

test('a cell that does not fit opens whole, formatted as what it is', async ({ page }) => {
  const long = (text: string) => text + ' '.repeat(2) + 'x'.repeat(400)
  const cells: Record<string, string> = {
    json: JSON.stringify({ a: { b: [1, 2] }, pad: 'x'.repeat(400) }),
    markdown: long('# A heading\n\n- one\n- two\n'),
    plain: 'just words '.repeat(60),
    cut: 'y'.repeat(5000) + '…',
  }
  let whole = 0
  await open(page, 'rows.jsonl', () => page.route('**/api/assets/table**', (route) => {
    const q = new URL(route.request().url()).searchParams
    if (q.get('full')) whole += 1
    return route.fulfill({ json: q.get('full')
      ? { columns: Object.keys(cells), rows: [['', '', '', 'the whole cell']], total: 1, offset: 0, limit: 1 }
      : { columns: Object.keys(cells), rows: [Object.values(cells), ['a', 'b', 'c', 'd']], total: 2, offset: 0, limit: 100 } })
  }))
  const cell = (column: number, row = 0) => page.locator('.table-view tbody tr').nth(row).locator('td').nth(column + 1)
  const modal = page.locator('.cell-modal')

  await cell(0).click()
  await expect(modal.locator('pre')).toContainText('"b": [\n')
  await modal.getByRole('button', { name: 'Close' }).click()
  await expect(modal).toHaveCount(0)

  await cell(1).click()
  await expect(modal.getByRole('heading', { name: 'A heading' })).toBeVisible()
  await expect(modal.locator('li')).toHaveCount(2)
  await page.keyboard.press('Escape')

  await cell(2).click()
  await expect(modal.locator('.cell-modal-text')).toContainText('just words')
  await page.mouse.click(5, 5)
  await expect(modal).toHaveCount(0)

  await cell(3).click()
  await expect(modal).toContainText('the whole cell')
  expect(whole).toBeGreaterThan(0)
  await page.keyboard.press('Escape')

  await cell(0, 1).click()  // it fits
  await expect(modal).toHaveCount(0)
})

test('a spreadsheet has a tab per sheet, each from its first page', async ({ page }) => {
  await open(page, 'book.xlsx')
  await page.getByLabel('Next page').click()
  await expect(first(page)).toHaveText('row 101')
  await page.getByRole('tab', { name: 'Two' }).click()
  await expect(page.getByRole('tab', { name: 'Two' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.table-view thead th').nth(2)).toHaveText('Two')
  await expect(first(page)).toHaveText('row 1')
})

test('a json list is a table, and any other json stays code', async ({ page }) => {
  await open(page, 'list.json')
  await expect(first(page)).toHaveText('row 1')
  await page.locator('section.viewer .back-btn').click()
  await page.locator('.asset-row', { hasText: 'plain.json' }).click()
  await expect(page.locator('section.viewer')).toContainText('plain')
  await expect(page.locator('.table-view')).toHaveCount(0)
})

test('an html file renders in a sandboxed frame, with its scripts', async ({ page }) => {
  await open(page, 'report.html')
  const frame = page.locator('iframe.source-frame')
  await expect(frame).toHaveAttribute('sandbox', 'allow-scripts allow-popups')
  await expect(page.frameLocator('iframe.source-frame').locator('#x')).toHaveText('Ran')
})
