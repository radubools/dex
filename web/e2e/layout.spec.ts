// Draggable pane dividers (web/src/split.ts): threads | chat | side pane on a
// wide screen, each width kept in this browser's localStorage. Runs the UI
// from the Vite dev server with the API answered in the browser from
// fixtures, so it needs no sign-in and touches nothing.
//
//   npx playwright test --config web/playwright.config.ts
import { expect, test, type Page } from '@playwright/test'

const now = 1_760_000_000
const task = {
  id: 'task-a', threadId: 't1', title: 'Build the widget', slug: 'widget', problem: 'Make it.', state: 'succeeded',
  activity: null, createdAt: now, startedAt: now, finishedAt: now, error: null, costUsd: 0.1, turns: 3,
  outputDir: 'alpha/widget', artifacts: [], pending: [], attempt: 1, parentId: null, sessionId: null, resumedFrom: null,
  outputSlug: 'widget', project: 'alpha', model: null, costIsEstimate: false, canResume: false, canRerun: true,
  canRestart: false, canPause: false, canContinue: false, canArchive: true,
}
const thread = {
  id: 't1', title: 'Widgets', project: 'alpha', kind: 'chat', createdAt: now, updatedAt: now, messageCount: 1, taskIds: ['task-a'],
}

function answer(url: URL): unknown {
  const p = url.pathname
  if (p === '/api/auth/me') return { state: 'open', user: null, googleEnabled: false, passwordEnabled: false }
  if (p === '/api/health') return { ok: true, workspace: '', assetsDir: '', model: 'test', concurrency: 1, manim: true, autoApprove: false, authenticated: true, running: 0, queued: 0 }
  if (p === '/api/settings') return { settings: { auto_approve: false }, costs: { day: 0, week: 0, month: 0, all_time: 0 } }
  if (p === '/api/tasks') return { tasks: [task] }
  if (p === '/api/tasks/task-a') return { task }
  if (p === '/api/tasks/task-a/events') return { taskId: 'task-a', events: [] }
  if (p === '/api/tasks/task-a/messages') return { messages: [] }
  if (p === '/api/projects') return { projects: [{ slug: 'alpha', name: 'Alpha', description: '', createdAt: now, updatedAt: now, threads: 1, tasks: 1 }], default: 'alpha' }
  if (p === '/api/threads') return { threads: [thread] }
  if (p === '/api/threads/t1') {
    return { thread: { ...thread, messages: [{ id: 'm1', role: 'dex', kind: 'tasks', text: 'Started 1 task', data: { tasks: [task] }, ts: now }] }, tasks: [task] }
  }
  return { ok: true }
}

async function open(page: Page, path = '/?project=alpha&thread=t1') {
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/events') return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(answer(url)) })
  })
  await page.goto(path)
  await expect(page.locator('.thread-row.on .thread-title')).toHaveText('Widgets')
}

const width = (page: Page, selector: string) => page.locator(selector).evaluate((el) => Math.round(el.getBoundingClientRect().width))
const stored = (page: Page, key: string) => page.evaluate((k) => localStorage.getItem(`dex.layout.${k}`), key)

/** Drags a divider sideways by `dx`, through pointer events as a mouse would. */
async function drag(page: Page, name: string, dx: number) {
  const box = (await page.getByRole('separator', { name }).boundingBox())!
  const x = box.x + box.width / 2
  const y = box.y + box.height / 2
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + dx / 2, y, { steps: 4 })
  await page.mouse.move(x + dx, y, { steps: 4 })
  await page.mouse.up()
}

test('the threads divider drags, survives a reload, and double-click resets it', async ({ page }) => {
  await open(page)
  expect(await width(page, '.threads')).toBe(280)
  await drag(page, 'Threads width', 80)
  expect(await width(page, '.threads')).toBe(360)
  expect(await stored(page, 'threads')).toBe('360')

  await page.reload()
  await expect(page.locator('.thread-row.on .thread-title')).toHaveText('Widgets')
  expect(await width(page, '.threads')).toBe(360)

  // Never narrower than its minimum, however far it is dragged.
  await drag(page, 'Threads width', -600)
  expect(await width(page, '.threads')).toBe(200)

  await page.getByRole('separator', { name: 'Threads width' }).dblclick()
  expect(await width(page, '.threads')).toBe(280)
  expect(await stored(page, 'threads')).toBeNull()
})

test('the side pane divider drags from the left, and arrow keys move it too', async ({ page }) => {
  await open(page)
  await page.locator('.task-chip', { hasText: 'Build the widget' }).first().click()
  await expect(page.locator('.task-panel .panel-name')).toHaveText('Build the widget')
  const before = await width(page, '.task-panel')
  // Dragging left widens a right-hand pane.
  await drag(page, 'Side pane width', -100)
  expect(await width(page, '.task-panel')).toBe(before + 100)
  expect(await stored(page, 'side')).toBe(String(before + 100))

  const handle = page.getByRole('separator', { name: 'Side pane width' })
  await expect(handle).toHaveAttribute('aria-orientation', 'vertical')
  await handle.focus()
  await page.keyboard.press('ArrowRight')
  expect(await width(page, '.task-panel')).toBe(before + 100 - 16)
  expect(await stored(page, 'side')).toBe(String(before + 100 - 16))

  // The chat keeps its minimum: the pane stops short of pushing it off screen.
  await drag(page, 'Side pane width', -2000)
  expect(await width(page, '.main')).toBeGreaterThanOrEqual(360)

  await page.reload()
  await page.locator('.task-chip', { hasText: 'Build the widget' }).first().click()
  expect(await width(page, '.main')).toBeGreaterThanOrEqual(360)
  expect(await width(page, '.task-panel')).toBe(Number(await stored(page, 'side')))
})

test('a stored size is clamped to the window, and a bad one falls back to the default', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('dex.layout.threads', '5000')
    localStorage.setItem('dex.layout.side', 'wide')
  })
  await open(page)
  expect(await width(page, '.threads')).toBe(480)
  await page.locator('.task-chip', { hasText: 'Build the widget' }).first().click()
  await expect(page.locator('.task-panel .panel-name')).toHaveText('Build the widget')
  // 34% of the 1280px window: the default.
  expect(await width(page, '.task-panel')).toBe(435)
})

test('a narrow window stacks the panes and has no dividers', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 })
  await page.addInitScript(() => localStorage.setItem('dex.layout.threads', '400'))
  await open(page, '/?project=alpha&thread=t1')
  await expect(page.getByRole('separator')).toHaveCount(0)
  expect(await width(page, '.main')).toBe(390)
  await page.locator('.task-chip', { hasText: 'Build the widget' }).first().click()
  expect(await width(page, '.task-panel')).toBe(390)
  await expect(page.getByRole('separator')).toHaveCount(0)
})
