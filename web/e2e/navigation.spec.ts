// Navigation history (web/src/navigation.ts): every pane the reader opens is
// a step that back walks through, in order, whether it is the browser's back
// or a pane's ‹. Runs the UI from the Vite dev server with the API answered
// in the browser from fixtures, so it needs no sign-in and touches nothing.
//
//   npx playwright test --config web/playwright.config.ts
import { expect, test, type Page } from '@playwright/test'

const now = 1_760_000_000
const project = (slug: string, name: string) => ({ slug, name, description: '', createdAt: now, updatedAt: now, threads: 2, tasks: 1 })
const thread = (id: string, title: string, proj: string, taskIds: string[] = []) => ({
  id, title, project: proj, kind: 'chat', createdAt: now, updatedAt: now, messageCount: 1, taskIds,
})
const task = {
  id: 'task-a', threadId: 't1', title: 'Build the widget', slug: 'widget', problem: 'Make it.', state: 'succeeded',
  activity: null, createdAt: now, startedAt: now, finishedAt: now, error: null, costUsd: 0.1, turns: 3,
  outputDir: 'alpha/widget', artifacts: [], pending: [], attempt: 1, parentId: null, sessionId: null, resumedFrom: null,
  outputSlug: 'widget', project: 'alpha', model: null, costIsEstimate: false, canResume: false, canRerun: true,
  canRestart: false, canPause: false, canContinue: false, canArchive: true,
}
const THREADS: Record<string, ReturnType<typeof thread>[]> = {
  alpha: [thread('t1', 'Widgets', 'alpha', ['task-a']), thread('t2', 'Ideas', 'alpha')],
  beta: [thread('t3', 'Beta talk', 'beta')],
}
const messages: Record<string, unknown[]> = {
  t1: [{ id: 'm1', role: 'dex', kind: 'tasks', text: 'Started 1 task', data: { tasks: [task] }, ts: now }],
  t2: [{ id: 'm2', role: 'user', kind: 'text', text: 'An idea', data: {}, ts: now }],
  t3: [{ id: 'm3', role: 'user', kind: 'text', text: 'Hello beta', data: {}, ts: now }],
}

/** The task graph's nodes (none unless a test sets them). */
let dag: unknown[] = []

function answer(url: URL): unknown {
  const p = url.pathname
  const q = url.searchParams
  if (p === '/api/auth/me') return { state: 'open', user: null, googleEnabled: false, passwordEnabled: false }
  if (p === '/api/health') return { ok: true, workspace: '', assetsDir: '', model: 'test', concurrency: 1, manim: true, autoApprove: false, authenticated: true, running: 0, queued: 0 }
  if (p === '/api/settings') return { settings: { auto_approve: false }, costs: { day: 0, week: 0, month: 0, all_time: 0 } }
  if (p === '/api/tasks') return { tasks: [task] }
  if (p === '/api/tasks/task-a') return { task }
  if (p === '/api/tasks/task-a/events') return { taskId: 'task-a', events: [] }
  if (p === '/api/tasks/task-a/messages') return { messages: [] }
  if (p === '/api/projects') return { projects: [project('alpha', 'Alpha'), project('beta', 'Beta')], default: 'alpha' }
  if (p === '/api/threads') return { threads: THREADS[q.get('project') ?? 'alpha'] ?? [] }
  const t = /^\/api\/threads\/(t\d)$/.exec(p)
  if (t) {
    const summary = Object.values(THREADS).flat().find((x) => x.id === t[1])!
    return { thread: { ...summary, messages: messages[t[1]] }, tasks: t[1] === 't1' ? [task] : [] }
  }
  if (p === '/api/packages') return { packages: [] }
  if (p === '/api/feed') return { project: 'alpha', projects: ['alpha'], topics: [] }
  if (p === '/api/dag') return { project: 'alpha', nodes: dag, edges: [] }
  if (/^\/api\/projects\/[^/]+\/skills$/.test(p)) return { skills: [] }
  if (/\/guide$/.test(p)) return { project: project('alpha', 'Alpha'), path: 'AGENTS.md', text: '# Alpha guide' }
  if (p === '/api/assets') {
    const path = q.get('path') ?? ''
    if (q.get('match')) {
      return { kind: 'dir', path, entries: ['notes.md', 'readme.md'].map((n) => ({ name: `${path}/${n}`, dir: false, kind: 'markdown', bytes: 10 })) }
    }
    return { kind: 'file', path, ext: 'md', content: `# ${path}` }
  }
  return { ok: true }
}

async function open(page: Page) {
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    // The live stream: nothing happens while the test runs.
    if (url.pathname === '/api/events') return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(answer(url)) })
  })
  await page.goto('/?project=alpha&thread=t1')
  await expect(thread_(page)).toHaveText('Widgets')
}

const thread_ = (page: Page) => page.locator('.thread-row.on .thread-title')

/** What is on screen: the thread, the task panel, the viewer's file, and which full pane is open. */
async function where(page: Page) {
  const dialog = page.locator('section.viewer[role="dialog"]')
  return {
    thread: (await thread_(page).textContent())?.trim() ?? null,
    task: (await page.locator('.task-panel .panel-name').count()) ? (await page.locator('.task-panel .panel-name').textContent())?.trim() : null,
    // A file's viewer is labelled with its path: its name is enough here.
    pane: (await dialog.count()) ? ((await dialog.first().getAttribute('aria-label')) ?? '').split('/').pop() : null,
    view: await page.locator('.view-toggle [aria-selected="true"]').getAttribute('aria-label'),
  }
}

const at = async (page: Page, expected: Partial<Awaited<ReturnType<typeof where>>>) => {
  await expect.poll(async () => {
    const w = await where(page)
    return Object.fromEntries(Object.keys(expected).map((k) => [k, w[k as keyof typeof w]]))
  }).toEqual(expected)
}

test('back walks through every pane that led here, in order, and forward again', async ({ page }) => {
  await open(page)
  const places = []
  const step = async (act: () => Promise<unknown>, expected: Parameters<typeof at>[1]) => {
    await act()
    await at(page, expected)
    places.push(expected)
  }
  places.push({ thread: 'Widgets', task: null, pane: null })
  await step(() => page.locator('.thread-open', { hasText: 'Ideas' }).click(), { thread: 'Ideas', task: null, pane: null })
  await step(() => page.locator('.thread-open', { hasText: 'Widgets' }).click(), { thread: 'Widgets', task: null, pane: null })
  await step(() => page.locator('.task-chip', { hasText: 'Build the widget' }).first().click(), { thread: 'Widgets', task: 'Build the widget', pane: null })
  await page.getByRole('button', { name: /^Files/ }).click()
  await step(() => page.locator('.asset-row', { hasText: 'notes.md' }).click(), { task: 'Build the widget', pane: 'notes.md' })
  await step(() => page.locator('section.viewer [aria-label="Close"]').click(), { task: 'Build the widget', pane: null })
  await step(() => page.locator('.asset-row', { hasText: 'readme.md' }).click(), { task: 'Build the widget', pane: 'readme.md' })
  await step(() => page.getByRole('tab', { name: 'Task graph' }).click(), { view: 'Task graph', task: 'Build the widget', pane: 'readme.md' })
  await step(() => page.getByRole('tab', { name: 'Chat' }).click(), { view: 'Chat', task: 'Build the widget', pane: 'readme.md' })
  await step(() => page.getByRole('button', { name: /^Guide/ }).click(), { pane: 'Project instructions' })

  // Back, one step at a time: the browser's button.
  for (let i = places.length - 2; i >= 0; i--) {
    await page.goBack()
    await at(page, places[i])
  }
  // Forward again, all the way.
  for (let i = 1; i < places.length; i++) {
    await page.goForward()
    await at(page, places[i])
  }
})

test("on a wide screen too, each pane's ‹ steps back through what led to it", async ({ page }) => {
  await open(page)
  await page.locator('.thread-open', { hasText: 'Ideas' }).click()
  await page.locator('.thread-open', { hasText: 'Widgets' }).click()
  await page.locator('.task-chip', { hasText: 'Build the widget' }).first().click()
  await page.getByRole('button', { name: /^Files/ }).click()
  await page.locator('.asset-row', { hasText: 'notes.md' }).click()
  await page.getByRole('tab', { name: 'Task graph' }).click()
  await page.getByRole('button', { name: /^Guide/ }).click()
  await at(page, { pane: 'Project instructions' })
  const backIn = (pane: string) => page.locator(`section[aria-label="${pane}"] .back-btn, section[aria-label$="${pane}"] .back-btn`).first()
  await expect(backIn('Project instructions')).toBeVisible()
  await backIn('Project instructions').click()
  await at(page, { view: 'Task graph', pane: 'notes.md' })
  // The graph is a view, not a pane: the browser's back leaves it.
  await page.goBack()
  await at(page, { view: 'Chat', task: 'Build the widget', pane: 'notes.md' })
  await backIn('notes.md').click()
  await at(page, { task: 'Build the widget', pane: null })
  await expect(page.locator('.task-panel .back-btn')).toBeVisible()
  await page.locator('.task-panel .back-btn').click()
  await at(page, { thread: 'Widgets', task: null, pane: null })
  // The browser's back carries on from there: the thread before.
  await page.goBack()
  await at(page, { thread: 'Ideas' })
})

test('the task graph is a main view; a task clicked in it opens beside it', async ({ page }) => {
  dag = [{ id: 'task-a', title: 'Build the widget', state: 'succeeded', attempt: 1, threadId: 't1', threadTitle: 'Widgets', createdAt: now, finishedAt: now }]
  try {
    await open(page)
    await page.locator('.thread-open', { hasText: 'Ideas' }).click()
    await page.getByRole('tab', { name: 'Task graph' }).click()
    await expect(page).toHaveURL(/view=graph/)
    await page.locator('.task-graph-group g.node').first().click()
    await at(page, { view: 'Task graph', thread: 'Widgets', task: 'Build the widget', pane: null })
    await expect(page.locator('section.task-graph')).toBeVisible()
    await expect(page.locator('.task-graph-group g.node.open')).toHaveCount(1)
    await page.goBack()
    await at(page, { view: 'Task graph', thread: 'Ideas', task: null })
  } finally {
    dag = []
  }
})

// On a phone the panes stack, and ‹ is the way out of each.
test.describe('on a phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })

test("a pane's ‹ goes back a step, not out of everything", async ({ page }) => {
  await open(page)
  await page.locator('.task-chip', { hasText: 'Build the widget' }).first().click()
  await page.getByRole('button', { name: /^Files/ }).click()
  await page.locator('.asset-row', { hasText: 'notes.md' }).click()
  await page.locator('section.viewer [aria-label="Close"]').click()
  await page.locator('.asset-row', { hasText: 'readme.md' }).click()
  await at(page, { task: 'Build the widget', pane: 'readme.md' })
  // The viewer's ‹: where it was opened from (the task, the viewer closed)…
  await page.locator('section.viewer .back-btn').click()
  await at(page, { task: 'Build the widget', pane: null })
  // …then the browser's back reopens the file before, whose ‹ goes back again.
  await page.goBack()
  await at(page, { task: 'Build the widget', pane: 'notes.md' })
  await page.locator('section.viewer .back-btn').click()
  await at(page, { task: 'Build the widget', pane: null })
  // The task panel's ‹: the thread as it was before the task opened.
  await page.locator('.task-panel .back-btn').click()
  await at(page, { thread: 'Widgets', task: null, pane: null })
})
})

test('views, projects and closing a pane are steps too; the first page is not left by ‹', async ({ page }) => {
  await open(page)
  await page.getByRole('tab', { name: 'Review feed' }).click()
  await at(page, { view: 'Review feed' })
  await page.goBack()
  await at(page, { view: 'Chat', thread: 'Widgets' })
  // A pane opened on the first page: its ‹ closes it rather than leaving dex.
  await page.goto('/?project=alpha&thread=t1')
  await at(page, { thread: 'Widgets' })
  await page.locator('.task-chip', { hasText: 'Build the widget' }).first().click()
  await at(page, { task: 'Build the widget' })
  await page.locator('.task-panel [aria-label="Close task"]').click()
  await at(page, { task: null })
  await page.goBack()
  await at(page, { task: 'Build the widget' })
  await page.reload()
  // A reload keeps the open panes.
  await at(page, { thread: 'Widgets', task: 'Build the widget' })
})
