// A task's activity (web/src/components/TaskPanel.tsx): it opens at its end,
// even when its history arrives after the panel opened, and ⤒ ⤓ jump to its
// start and end. The API is answered in the browser from fixtures.
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
const thread = { id: 't1', title: 'Widgets', project: 'alpha', kind: 'chat', createdAt: now, updatedAt: now, messageCount: 1, taskIds: ['task-a'] }
// Enough activity to scroll several screens.
const events = Array.from({ length: 80 }, (_, i) => ({
  type: 'text', taskId: 'task-a', seq: i + 1, ts: now + i,
  text: `Step ${i + 1}: the agent wrote a paragraph long enough to wrap onto a second line in the panel.`,
}))

function answer(p: string): unknown {
  if (p === '/api/auth/me') return { state: 'open', user: null, googleEnabled: false, passwordEnabled: false }
  if (p === '/api/health') return { ok: true, workspace: '', assetsDir: '', model: 'test', concurrency: 1, manim: true, autoApprove: false, authenticated: true, running: 0, queued: 0 }
  if (p === '/api/settings') return { settings: { auto_approve: false }, costs: { day: 0, week: 0, month: 0, all_time: 0 } }
  if (p === '/api/tasks') return { tasks: [task] }
  if (p === '/api/tasks/task-a') return { task }
  if (p === '/api/tasks/task-a/messages') return { messages: [] }
  if (p === '/api/projects') return { projects: [{ slug: 'alpha', name: 'Alpha', description: '', createdAt: now, updatedAt: now, threads: 1, tasks: 1 }], default: 'alpha' }
  if (p === '/api/threads') return { threads: [thread] }
  if (p === '/api/threads/t1') return { thread: { ...thread, messages: [{ id: 'm1', role: 'dex', kind: 'tasks', text: 'Started 1 task', data: { tasks: [task] }, ts: now }] }, tasks: [task] }
  if (p === '/api/packages') return { packages: [] }
  if (/\/skills$/.test(p)) return { skills: [] }
  return { ok: true }
}

async function open(page: Page) {
  await page.route('**/api/**', async (route) => {
    const p = new URL(route.request().url()).pathname
    if (p === '/api/events') return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
    // The history comes late, as it does from a busy server.
    if (p === '/api/tasks/task-a/events') {
      await new Promise((r) => setTimeout(r, 600))
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ taskId: 'task-a', events }) })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(answer(p)) })
  })
  await page.goto('/?project=alpha&thread=t1')
  await page.locator('.task-chip', { hasText: 'Build the widget' }).first().click()
}

const body = (page: Page) => page.locator('.task-panel .panel-body')
const fromEnd = (page: Page) => body(page).evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight)
const fromTop = (page: Page) => body(page).evaluate((el) => el.scrollTop)

test('the activity opens at its end, even when it arrives late, and ⤒ ⤓ jump to its start and end', async ({ page }) => {
  await open(page)
  await expect(page.locator('.task-panel .feed')).toContainText('Step 80')
  await expect.poll(() => fromEnd(page)).toBeLessThan(4)
  expect(await body(page).evaluate((el) => el.scrollHeight > el.clientHeight * 2)).toBe(true)

  await page.getByRole('button', { name: 'Scroll to the top' }).click()
  await expect.poll(() => fromTop(page)).toBe(0)
  await page.getByRole('button', { name: 'Scroll to the bottom' }).click()
  await expect.poll(() => fromEnd(page)).toBeLessThan(4)
})

test('scrolling up stops it following the end', async ({ page }) => {
  await open(page)
  await expect.poll(() => fromEnd(page)).toBeLessThan(4)
  await body(page).evaluate((el) => el.scrollTo({ top: 100 }))
  // The panel growing (here: a wider-to-narrower relayout) does not pull it back down.
  await page.setViewportSize({ width: 1180, height: 800 })
  await page.waitForTimeout(300)
  expect(await fromTop(page)).toBeLessThan(400)
})
